from pydantic import BaseModel, HttpUrl
import boto3
import requests
import io
from fastapi.responses import FileResponse
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from PIL import Image
import numpy as np
from fastapi import FastAPI, HTTPException, Header
from typing import Optional, List
from datetime import datetime

app = FastAPI()

# AWS S3 Client configuration
s3_client = boto3.client('s3')

class PhotoLink(BaseModel):
    urls: List[HttpUrl]
    age: int
    gender: str
    patientName: str

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

@app.post("/upload_dcm_files/")
async def upload_dcm_files(token: Optional[str] = Header(None), data: PhotoLink = None):
    
    # payload = decode_token(token)
    # if not payload or payload == 'unauthenticated':
    #     raise HTTPException(status_code=404, detail="Unauthenticated")

    try:
        uploaded_results = []

        # Iterate over each URL and process the image
        for url in data.urls:
            # Fetch the image from the provided URL
            response = requests.get(url)

            if response.status_code != 200:
                raise HTTPException(status_code=404, detail=f"Image not found for URL: {url}")
            
            # Convert the image bytes directly to DICOM
            dcm_content = convert_jpg_to_dicom(response.content, data)

            # Send the generated DICOM file to the Orthanc server
            orthanc_response = requests.post(
                f"https://dev-pacs.smaro.app/orthanc/instances",
                dcm_content,
                headers={'Content-Type': 'application/octet-stream'},
                auth=('orthanc', 'Orthanc@1234')
            )

            if orthanc_response.status_code == 200:
                orthanc_data = orthanc_response.json()
                study = orthanc_data[0] if isinstance(orthanc_data, list) else orthanc_data
                result = await fetch_study_id(Study(ParentStudy=study['ParentStudy']))

                if not result:
                    raise HTTPException(status_code=404, detail=f"Not able to fetch the patient study for image URL: {url}")
                
                uploaded_results.append({"url": url, "result": result})
            else:
                raise HTTPException(status_code=404, detail=f"Failed to upload the patient file for image URL: {url}")

        return {"status_code": 200, "data": uploaded_results, "message": "Successfully uploaded all images"}

    except Exception as err:
        print(err)
        raise HTTPException(status_code=500, detail="Something went wrong, please try again later")

def convert_jpg_to_dicom(image_bytes: bytes, data: PhotoLink = None) -> bytes:
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
    ds.patient_name = data.patientName
    ds.gender = data.gender
    ds.age = data.age
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
