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
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
import time
 
app = FastAPI()
# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
load_dotenv()



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
            f"{os.getenv("orthanc_URL")}/studies/{study.ParentStudy}",
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
        # Fetch and convert all images into one DICOM file with multiple frames
        dcm_content = await convert_multiple_images_to_dicom(data.urls, data)

        # Send the generated DICOM file to the Orthanc server
        orthanc_response = requests.post(
            f"{os.getenv('orthanc_URL')}/instances",
            dcm_content,
            headers={'Content-Type': 'application/octet-stream'},
            auth=('orthanc', 'Orthanc@1234')
        )

        if orthanc_response.status_code == 200:
            orthanc_data = orthanc_response.json()
            study = orthanc_data[0] if isinstance(orthanc_data, list) else orthanc_data
            result = await fetch_study_id(Study(ParentStudy=study['ParentStudy']))

            if not result:
                raise HTTPException(status_code=404, detail="Not able to fetch the patient study for images")
            
            return {"status_code": 200, "data": result, "message": "Successfully uploaded all images"}

        else:
            raise HTTPException(status_code=404, detail="Failed to upload the patient file")

    except Exception as err:
        print(err)
        raise HTTPException(status_code=500, detail="Something went wrong, please try again later")

async def convert_multiple_images_to_dicom(image_urls: List[HttpUrl], data: PhotoLink = None) -> bytes:
    """
    Converts multiple JPEG images to a single multi-frame DICOM file and returns it as bytes.
    """
    timestamp = str(time.time_ns())
    # Take the last 6 digits of the timestamp
    unique_id = timestamp[-6:]
    # Create a DICOM dataset
    ds = Dataset()

    # Fill in some required values
    ds.PatientName = data.patientName
    ds.PatientID = unique_id
    ds.PatientSex = data.gender
    ds.age = data.age
    ds.Modality = "OT"  # Other
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture Image Storage
    

    
    # Filling in the date
 

    # Collect pixel data from all images
    pixel_data_list = []

    for url in image_urls:
        response = requests.get(url)
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Image not found for URL: {url}")
        
        # Open the JPEG image from bytes using Pillow
        img = Image.open(io.BytesIO(response.content))
        img = img.convert('L')  # Convert to grayscale

        # Convert the image to a numpy array
        np_img = np.array(img)

        # Append to the pixel data list
        pixel_data_list.append(np_img.tobytes())
    
    # Combine all frames into one byte string for multi-frame DICOM
    combined_pixel_data = b''.join(pixel_data_list)

    # Set the pixel data
    ds.PixelData = combined_pixel_data
    ds.Rows, ds.Columns = np_img.shape
    ds.NumberOfFrames = len(image_urls)

    # Set the necessary DICOM tags
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelSpacing = [1, 1]
    ds.BitsStored = 8
    ds.BitsAllocated = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.ImageType = ["ORIGINAL", "PRIMARY", "OTHER"]

    # Set the transfer syntax
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    # Save the DICOM file to a bytes buffer instead of a file
    dicom_bytes_io = io.BytesIO()
    ds.save_as(dicom_bytes_io)
    
    # Return the bytes
    dicom_bytes_io.seek(0)  # Go to the beginning of the BytesIO buffer
    return dicom_bytes_io.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
