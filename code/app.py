import asyncio
from io import BytesIO
import json
import os
import time
from dotenv import load_dotenv
from azure.cognitiveservices.vision.customvision.prediction import CustomVisionPredictionClient
from azure.iot.device.aio import IoTHubDeviceClient, ProvisioningDeviceClient
from azure.iot.device import MethodRequest, MethodResponse
from msrest.authentication import ApiKeyCredentials

from picamera import PiCamera
import cv2

# Load the connection details from IoT Central for the device
load_dotenv()
id_scope = os.environ["ID_SCOPE"]
device_id = os.environ["DEVICE_ID"]
primary_key = os.environ["PRIMARY_KEY"]
camera_type = os.environ["CAMERA_TYPE"]
prediction_url = os.environ["PREDICTION_URL"]
prediction_key = os.environ["PREDICTION_KEY"]

# Decompose the prediction URL
parts = prediction_url.split('/')
endpoint = 'https://' + parts[2]
project_id = parts[6]
iteration_name = parts[9]

# Create the image classifier predictor
prediction_credentials = ApiKeyCredentials(in_headers={"Prediction-key": prediction_key})
predictor = CustomVisionPredictionClient(endpoint, prediction_credentials)

camera = None

# Sets up the PiCamera if this is the camera being used
def setup_picamera() -> PiCamera:
    camera = PiCamera()
    camera.resolution = (640, 480)
    camera.rotation = 180

    time.sleep(2)

    return camera

# Sets up OpenCV if a USB webcam is being used
def setup_opencv() -> None:
    return cv2.VideoCapture(0)

# Set up the relevant type of camera
if camera_type.lower() == "picamera":
    camera = setup_picamera()
else:
    camera = setup_opencv()

# Take a picture
def take_picture() -> BytesIO:
    if camera_type.lower() == "picamera":
        # If we are using the PiCamera, capture a jpeg directly into a BytesIO object
        image = BytesIO()
        camera.capture(image, 'jpeg')
        # Rewind the BytesIO and return it
        image.seek(0)
        return image
    else:
        # If we are using a USB webcam, capture an image using OpenCV
        _, image = camera.read()
        # Encode the image as a JPEG into a byte buffer
        _, buffer = cv2.imencode(".jpg", image)
        # Copy the byte buffer into a BytesIO object and retturn it
        return BytesIO(buffer)

def classify_image(image: BytesIO) -> str:
    # Send the image to the classifier to get the predictions
    results = predictor.classify_image(project_id, iteration_name, image)

    best_prediction = results.predictions[0]

    # print the predictions and find the one with the highest probability
    print("Predictions:")
    for prediction in results.predictions:
        print(f'{prediction.tag_name}:\t{prediction.probability * 100:.2f}%')
        if prediction.probability > best_prediction.probability:
            best_prediction = prediction
    
    return best_prediction.tag_name

async def connect_device() -> IoTHubDeviceClient:
    # Connect to the device provisioning service and request the connection details for the device
    provisioning_device_client = ProvisioningDeviceClient.create_from_symmetric_key(
        provisioning_host="global.azure-devices-provisioning.net",
        registration_id=device_id,
        id_scope=id_scope,
        symmetric_key=primary_key)
    registration_result = await provisioning_device_client.register()

    # Build the connection string - this is used to connect to IoT Central
    conn_str="HostName=" + registration_result.registration_state.assigned_hub + \
                ";DeviceId=" + device_id + \
                ";SharedAccessKey=" + primary_key

    # The device client object is used to interact with Azure IoT Central.
    device_client = IoTHubDeviceClient.create_from_connection_string(conn_str)

    # Connect the device client
    print("Connecting")
    await device_client.connect()
    print("Connected")

    # Return the device client
    return device_client

# The main app loop that keeps the app alive
async def main() -> None:
    # Create the IoT device client
    device_client = await connect_device()

    # Send telemetry to IoT Central with the detected breed
    async def send_telemetry(telemetry: str) -> None:
        await device_client.send_message(telemetry)

    # Define a callback that is called when a command is received from IoT Central
    async def method_request_handler(method_request: MethodRequest) -> None:
        print("Command received:", method_request.name)
        
        # Define a return status - 404 for not found unless the command name is one we know about
        status = 404

        # If the detect breed command is invoked, use the camera to detect the breed
        if method_request.name == "DetectBreed":
            status = 200

            # Take a picture
            image = take_picture()

            # Get the highest predted breed from the picture
            breed = classify_image(image)

            print("Breed detected:", breed)
            
            # Send the breed to IoT Central
            telemetry = {"Breed": breed}
            await send_telemetry(json.dumps(telemetry))

        # Send a response - all commands need a response
        method_response = MethodResponse.create_from_method_request(method_request, status, {})
        await device_client.send_method_response(method_response)

    # Connect the command handler
    device_client.on_method_request_received = method_request_handler

    # Loop forever
    while True:
        await asyncio.sleep(60)

# Start the app running
asyncio.run(main())