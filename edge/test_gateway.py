import requests

url = "http://localhost:8084/predict"

with open("./edge/sample_images/test.jpg", "rb") as f:
    files = {"file": f}
    r = requests.post(url, files=files)

print("Status:", r.status_code)
print("Raw response:")
print(r.text)