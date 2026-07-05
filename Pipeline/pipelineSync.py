from JamfAPI import JAMFAPIHandler, getAllResults
from typing import Dict, List, Optional
from requests import HTTPError, Timeout
from re import search
from pathlib import Path
from datetime import datetime, timedelta, UTC
import os

ENV = os.environ

def discoverFiles(rootDir: Path) -> List[Path]:
    outputList: List[Path] = []
    for dir in rootDir.iterdir():
        if dir.is_file():
            outputList.append(dir)
            print(f'Discovered {dir}')
        else:
            outputList.extend(discoverFiles(dir))
    return outputList

def deletionStamp() -> str:
    delete_time = datetime.now(UTC) + timedelta(hours=72)
    return f"__delete__={delete_time.isoformat()}"

def createScriptObject(scriptPath: Path, categories: Dict) -> Dict:
    try:
        finalDir = scriptPath.parts[-2]
    except IndexError:
        finalDir = ''
    if finalDir in categories:
        name = finalDir
        id = categories[finalDir]['id']
    else:
        name = 'None'
        id = '0'
    
    with scriptPath.open() as f:
        return {
            'name': scriptPath.name,
            'categoryName': name,
            'categoryId': id,
            'scriptContents': f.read()
        }

def compareScriptsObjects(a: Dict, b: Dict) -> bool:
    return a['categoryName'] == b['categoryName'] and a['scriptContents'] == b['scriptContents']

def checkForDeletionStamp(notesTag: str) -> Optional[datetime]:
    if not notesTag:
        return None
    # Support both:
    # 2026-01-23 23:05:07.454633+00:00
    # 2026-01-23T23:05:07.454633+00:00
    match = search(
        r'__delete__=(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{1,6}\+\d{2}:\d{2})',
        notesTag
    )
    return None if not match else datetime.fromisoformat(match.group(1))

DELETE_RE = r'__delete__=\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{1,6}\+\d{2}:\d{2}'
def stripDeletionMarker(text: Optional[str]) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    cleaned = [line for line in lines if not search(DELETE_RE, line)]
    return "\n".join(cleaned).strip()

def syncScripts(api: JAMFAPIHandler, categories: Dict) -> None:
    try:
        deployedScripts = {script['name']: script for script in getAllResults(api.getScripts)}
        print('Retrieved scripts deployed to JAMF.')
    except HTTPError as httpe:
        print(f'Could not retrieve scripts - server responded with HTTP error {httpe.response.status_code}:')
        print(httpe.response.json()['errors'])
        print('Cannot sync with incomplete comparison, skipping script sync.')
        return
    except Timeout as timeout:
        print('Request to retrieve deployed scripts timed out. Skipping script sync.')
        return
    
    syncFiles = discoverFiles(Path('Scripts'))

    print('Pushing script files to JAMF ...')
    for scriptFile in syncFiles:
        scriptObject = createScriptObject(scriptFile, categories)
        if scriptFile.name in deployedScripts:
            # If it exists in Git, remove any stale __delete__ marker
            deployed = deployedScripts[scriptFile.name]
            cleaned_notes = stripDeletionMarker(deployed.get("notes"))
            if cleaned_notes != (deployed.get("notes") or ""):
                deployed["notes"] = cleaned_notes
                try:
                    api.putScript(deployed["id"], deployed)
                    print(f"Removed deletion marker from script {scriptFile.name}.")
                except:
                    print(f"Could not remove deletion marker from script {scriptFile.name}. Skipping undelete cleanup.")
            if not compareScriptsObjects(scriptObject, deployed):
                deployed.update(scriptObject)
                try:
                    api.putScript(deployed['id'], deployed)
                    print(f'Pushed updated script {scriptFile.name} to JAMF.')
                except:
                    print(f'Could not update script {scriptFile.name} on JAMF. Skipping.')
        elif scriptFile.name not in deployedScripts:
            try:
                api.postScript(scriptObject)
                print(f'Pushed new script {scriptFile.name} to JAMF.')
            except:
                print(f'Could not push new file {scriptFile} to JAMF. Skipping.')
    syncFileNames = [file.name for file in syncFiles]
    print('Marking for deletion ...')
    for name, value in deployedScripts.items():
        if name not in syncFileNames:
            timestamp = checkForDeletionStamp(value.get('notes') or "")
            if timestamp is None:
                try:
                    deleteStamp = deletionStamp()
                    value['notes'] = (value.get('notes') or "").rstrip() + "\n" + deleteStamp
                    api.putScript(value['id'], value)
                    print(f'Script {name} marked for deletion after {deleteStamp}.')
                except:
                    print(f'Could not mark script {name} for deletion. You are safe for now ...')

EA_INVENTORY_DISPLAY_TYPES = {
    "GENERAL",
    "HARDWARE",
    "OPERATING_SYSTEM",
    "USER_AND_LOCATION",
    "PURCHASING",
    "EXTENSION_ATTRIBUTES",
}

def parseEAHeaders(scriptContents: str) -> Dict:
    """
    Parse EA headers from script comments.
    Supported headers:
      EA-Description
      EA-DataType (STRING|INTEGER|DATE)
    """
    description = None   # default
    dataType = "STRING"  # default

    for line in scriptContents.splitlines():
        line = line.strip()
        # Stop parsing once we hit real code (non-empty, non-comment)
        if line and not line.startswith("#"):
            break
        if line.startswith("#"):
            header = line.lstrip("#").strip()
            if header.startswith("EA-Description:"):
                description = header.split(":", 1)[1].strip()
            elif header.startswith("EA-DataType:"):
                dataType = header.split(":", 1)[1].strip().upper()
    # Validate datatype (fallback safely)
    if dataType not in {"STRING", "INTEGER", "DATE"}:
        print(f"WARNING: Invalid EA-DataType '{dataType}'. Defaulting to STRING.")
        dataType = "STRING"
    return {"description": description, "dataType": dataType}

def createComputerEAObject(eaPath: Path) -> Dict:
    """
    Folder structure:
      ExtensionAttributes/<INVENTORY_DISPLAY_TYPE>/<file>

    Script headers:
      # EA-Description: ...
      # EA-DataType: STRING|INTEGER|DATE
    """
    inventoryDisplayType = eaPath.parent.name.upper()
    if inventoryDisplayType not in EA_INVENTORY_DISPLAY_TYPES:
        print(
            f"WARNING: {eaPath} is under '{eaPath.parent.name}', "
            "which is not a valid inventoryDisplayType. Defaulting to GENERAL."
        )
        inventoryDisplayType = "GENERAL"
    with eaPath.open("r", encoding="utf-8") as f:
        contents = f.read()
    headerData = parseEAHeaders(contents)
    return {
        "name": eaPath.stem,
        "description": headerData.get("description") or f"Managed by GitLab CI from {eaPath.as_posix()}",
        "dataType": headerData.get("dataType", "STRING"),
        "enabled": True,  # always on by default
        "inventoryDisplayType": inventoryDisplayType,
        "inputType": "SCRIPT",
        "scriptContents": contents,
    }

def compareComputerEAObjects(desired: Dict, deployed: Dict) -> bool:
    return (
        deployed.get("inputType") == "SCRIPT"
        and desired.get("scriptContents") == deployed.get("scriptContents")
        and desired.get("enabled") == deployed.get("enabled")
        and desired.get("dataType") == deployed.get("dataType")
        and desired.get("inventoryDisplayType") == deployed.get("inventoryDisplayType")
        and desired.get("description") == deployed.get("description")
    )

def syncComputerExtensionAttributes(api: JAMFAPIHandler) -> None:
    try:
        deployedEAs = {ea["name"]: ea for ea in getAllResults(api.getComputerExtensionAttributes)}
        print("Retrieved Computer Extension Attributes deployed to JAMF.")
    except HTTPError as httpe:
        print(f"Could not retrieve Computer EAs - HTTP {httpe.response.status_code}:")
        print(httpe.response.json().get("errors", httpe.response.text))
        print("Skipping Computer Extension Attributes sync.")
        return
    except Timeout:
        print("Request to retrieve Computer EAs timed out. Skipping Computer Extension Attributes sync.")
        return

    eaRoot = Path("ExtensionAttributes")
    # include files under ExtensionAttributes/<TYPE>/..., exclude files directly under ExtensionAttributes/
    eaFiles = [p for p in discoverFiles(eaRoot) if p.is_file() and p.parent != eaRoot]
    desiredEANames = {createComputerEAObject(p)["name"] for p in eaFiles}

    if not eaFiles:
        print("No EA scripts found under ExtensionAttributes/<TYPE>/. Skipping EA sync.")
        return

    print("Pushing Computer Extension Attribute scripts to JAMF ...")
    for eaFile in eaFiles:
        desired = createComputerEAObject(eaFile)
        name = desired["name"]

        if name in deployedEAs:
            ea_id = str(deployedEAs[name]["id"])
            try:
                deployedFull = api.getComputerExtensionAttributeByID(ea_id).json()
                # If it exists in Git, remove stale __delete__ marker
                if deployedFull.get("inputType") == "SCRIPT":
                    cleaned_desc = stripDeletionMarker(deployedFull.get("description"))
                    if cleaned_desc != (deployedFull.get("description") or ""):
                        deployedFull["description"] = cleaned_desc
                        api.putComputerExtensionAttribute(ea_id, deployedFull)
                        print(f"Removed deletion marker from Computer EA {name}.")
                if not compareComputerEAObjects(desired, deployedFull):
                    api.putComputerExtensionAttribute(ea_id, desired)
                    print(f"Updated Computer EA: {name}")
            except HTTPError as e:
                r = e.response
                print(f"Could not update Computer EA {name}: HTTP {r.status_code}")
                try:
                    print(r.json())
                except Exception:
                    print(r.text)
            except Exception as e:
                print(f"Could not update Computer EA {name}: {e}")
        else:
            try:
                api.postComputerExtensionAttribute(desired)
                print(f"Created Computer EA: {name}")
            except HTTPError as e:
                r = e.response
                print(f"Could not create Computer EA {name}: HTTP {r.status_code}")
                try:
                    print(r.json())
                except Exception:
                    print(r.text)
            except Exception as e:
                print(f"Could not create Computer EA {name}: {e}")
    print("Marking missing Computer Extension Attributes for deletion ...")
    for name, deployed in deployedEAs.items():
        if name not in desiredEANames:
            try:
                ea_id = str(deployed["id"])
                deployedFull = api.getComputerExtensionAttributeByID(ea_id).json()

                updated = markEAForDeletion(deployedFull)
                if updated != deployedFull:
                    api.putComputerExtensionAttribute(ea_id, updated)
                    print(f"Marked Computer EA {name} for deletion.")
            except Exception:
                print(f"Could not mark Computer EA {name} for deletion. Skipping.")

def markEAForDeletion(ea: Dict) -> Dict:
    """
    Add __delete__ marker to EA description if:
      - inputType == SCRIPT
      - marker does not already exist
    """
    if ea.get("inputType") != "SCRIPT":
        return ea
    description = ea.get("description") or ""
    if "__delete__=" in description:
        return ea
    marker = deletionStamp()
    new_description = f"{description}\n{marker}".strip()
    updated = ea.copy()
    updated["description"] = new_description
    return updated

def main():
    if ENV['CI_COMMIT_BRANCH'] == 'prod':
        API_URL = ENV["JAMF_URL_PROD"]
        client_id = ENV["JAMF_CLIENT_ID_PROD"]
        client_secret = ENV["JAMF_CLIENT_SECRET_PROD"]
    elif ENV['CI_COMMIT_BRANCH'] == 'test':
        API_URL = ENV["JAMF_URL_TEST"]
        client_id = ENV["JAMF_CLIENT_ID_TEST"]
        client_secret = ENV["JAMF_CLIENT_SECRET_TEST"]
    else:
        print(f'Pipeline should not run on {ENV["CI_COMMIT_BRANCH"]}. Exiting ...')
        exit(1)
    
    print(f'Target URL: {API_URL}')

    try:
        with JAMFAPIHandler(API_URL, client_id, client_secret) as api:
            print("Authenticated to JAMF API ...")
            categories = {category['name']: category for category in getAllResults(api.getCategories)}
            print("Retrieved JAMF categories.")
            syncScripts(api, categories)
            syncComputerExtensionAttributes(api)
    except HTTPError as httpe:
        print(f'{httpe.request.method} {httpe.request.url} failed with HTTP status code {httpe.response.status_code}.')
        print(httpe.response.json()['errors'])
        exit(httpe.errno)
    except Timeout as timeout:
        print(f'Request {timeout.request.method} {timeout.request.url} timed out.')
        exit(timeout.errno)



if __name__ == '__main__':
    main()
