import json
import os
import Config

filename = "TO-DO"

default_payload = {
        "client_name": "TO-DO",
        "device_name": "TO-DO",
        "device_serial": "TO-DO",
        "logs":[],
        "errors":[],
        "consumables": [],
    }

payload = {}

errors = []
logs = []
consumables = []

def setup():
    global default_payload, filename, payload, consumables
    # load existing file OR create new one
    if os.path.exists(filename):  
        with open(filename, "r") as f:
            payload = json.load(f)
    else:
        payload = default_payload
        with open(filename, "w") as f:
            json.dump(payload, f, indent=4)
    consumables = payload["consumables"]

def update_consumable(name, value):
    global consumables
    # try to find existing entry
    for item in consumables:
        if item["name"] == name:
            item["value"] = value
            return  # stop here if updated

    # not found ? add new one
    consumables.append({
        "name": name,
        "value": value
    })

def add_error(type, message):
    errors.append({
        "error_type": type,
        "message": message
    })

def add_log(message):
    logs.append(message)

def save_payload():
    with open(filename, "r") as f:
            payload = json.load(f)
    payload["errors"].extend(errors)
    payload["logs"].extend(logs)
    payload["consumables"] = consumables
    with open(filename, "w") as f:
        json.dump(payload, f, indent=4)
    errors.clear()
    logs.clear()

def get_consumable_value(name):
    for item in payload["consumables"]:
        if item["name"] == name:
            return item["value"]
    return None
