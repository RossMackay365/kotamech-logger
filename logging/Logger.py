import json
import os

filename = "fileName.json"

default_payload = {
        "client_name": "TO-DO",
        "device_serial": "TO-DO",
        "logs":[],
        "errors":[],
        "consumables": [],
    }

payload = {}

def setup():
    global default_payload, filename, payload
    # load existing file OR create new one
    if os.path.exists(filename):  
        with open(filename, "r") as f:
            payload = json.load(f)
    else:
        payload = default_payload
        with open(filename, "w") as f:
            json.dump(payload, f, indent=4)

def update_consumable(name, value):
    global payload
    # try to find existing entry
    for item in payload["consumables"]:
        if item["name"] == name:
            item["value"] = value
            return  # stop here if updated

    # not found ? add new one
    payload["consumables"].append({
        "name": name,
        "value": value
    })

def add_error(type, message):
    payload["errors"].append({
        "error_type": type,
        "message": message
    })

def add_log(message):
    payload["logs"].append(message)

def save_payload():
    with open(filename, "w") as f:
        json.dump(payload, f, indent=4)

def clear_logs_and_errors():
    payload["logs"] = []
    payload["errors"] = []
    save_payload()

def get_consumable_value(name):
    for item in payload["consumables"]:
        if item["name"] == name:
            return item["value"]
    return None
