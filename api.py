
from pydantic import BaseModel, HttpUrl
import boto3
import requests
import tempfile
import io
from fastapi.responses import FileResponse
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from PIL import Image
import numpy as np
from fastapi import FastAPI, HTTPException, Header
from typing import Optional
import os
from datetime import datetime

app = FastAPI()

# AWS S3 Client configuration
s3_client = boto3.client('s3')

class PhotoLink(BaseModel):
    url: HttpUrl


class Study(BaseModel):
    ParentStudy: str
    StudyInstanceUID: Optional[str] = None

# Assuming this function generates and returns the DICOM file content in bytes


async def fetch_study_id(study: Study):
    try:
        response = requests.get(
            f"https://dev-pacs.smaro.app/orthanc/studies/{study.ParentStudy}",
            headers={'Content-Type': 'application/json'},
            auth=('orthanc', 'Orthanc@1234')
        )
        if response.status_code == 200:
            data = response.json()
            return {**study.dict(), 'StudyInstanceUID': data.get('MainDicomTags', {}).get('StudyInstanceUID')}
        else:
            return False
    except Exception as e:
        print(e)
        return False

def decode_token(token: str):
    # Replace with your actual token decoding logic
    if token == "valid-token":
        return {"user_id": 1}
    else:
        return "unauthenticated"

@app.post("/upload_dcm_file/")
async def upload_dcm_file(token: Optional[str] = Header(None), link: PhotoLink = None):
    
    # payload = decode_token(token)
    # if not payload or payload == 'unauthenticated':
    #     raise HTTPException(status_code=404, detail="Unauthenticated")

    try:
        # Fetch the image from the provided URL
        response = requests.get(link.url)
    
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="Image not found")
    
        # Convert the image bytes directly to DICOM
        dcm_content = convert_jpg_to_dicom(response.content)

        # Send the generated DICOM file to the Orthanc server
        response = requests.post(
            f"https://dev-pacs.smaro.app/orthanc/instances",
            dcm_content,
            headers={'Content-Type': 'application/octet-stream'},
            auth=('orthanc', 'Orthanc@1234')
        )

        if response.status_code == 200:
            data = response.json()
            study = data[0] if isinstance(data, list) else data
            result = await fetch_study_id(Study(ParentStudy=study['ParentStudy']))

            if not result:
                raise HTTPException(status_code=404, detail="Not able to fetch the patient study")

            return {"status_code": 200, "data": [result, data], "message": "Successfully uploaded details"}
        else:
            raise HTTPException(status_code=404, detail="Failed to upload the patient file")

    except Exception as err:
        print(err)
        raise HTTPException(status_code=500, detail="Something went wrong, please try again later")




# @app.post("/upload/")
# async def process_photo(link: PhotoLink):
#     # Fetch the image from the S3 bucket or directly from the link
#     response = requests.get(link.url)
    
#     if response.status_code != 200:
#         raise HTTPException(status_code=404, detail="Image not found")
    
#     # Save the image to a temporary file
#     with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_image_file:
#         temp_image_file.write(response.content)
#         temp_image_file_path = temp_image_file.name

#     # Convert the JPEG image to a DICOM file
#     dicom_bytes = convert_jpg_to_dicom(temp_image_file_path)

#     # Return the DICOM file as a response
#     return FileResponse(dicom_bytes, media_type='application/dicom', filename='image.dcm')

def convert_jpg_to_dicom(image_bytes: bytes) -> bytes:
    """
    Converts a JPEG image (in bytes) to a DICOM file and returns it as bytes.
    """
    # Open the JPEG image from bytes using Pillow
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert('L')  # Convert to grayscale

    # Convert the image to a numpy array
    np_img = np.array(img)

    # Create a DICOM dataset
    ds = Dataset()

    # Fill in some required values
    ds.PatientName = "Test^Test"
    ds.PatientID = "123456"
    ds.Modality = "OT"  # Other
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture Image Storage
    
    #Filling in the date
    current_date = datetime.now().strftime('%Y%m%d')  # Format: YYYYMMDD
    ds.StudyDate = current_date
    ds.SeriesDate = current_date
    ds.AcquisitionDate = current_date
    ds.ContentDate = current_date

    # Set the transfer syntax
    ds.is_little_endian = True
    ds.is_implicit_VR = True

    # Set the image data
    ds.PixelData = np_img.tobytes()
    ds.Rows, ds.Columns = np_img.shape

    # Set the necessary DICOM tags
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelSpacing = [1, 1]
    ds.BitsStored = 8
    ds.BitsAllocated = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.ImageType = ["ORIGINAL", "PRIMARY", "OTHER"]

    # Save the DICOM file to a bytes buffer instead of a file
    dicom_bytes_io = io.BytesIO()
    ds.save_as(dicom_bytes_io)
    
    # Return the bytes
    dicom_bytes_io.seek(0)  # Go to the beginning of the BytesIO buffer
    return dicom_bytes_io.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
