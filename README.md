# Jamf as Code

Managing Jamf Pro scripts and Extension Attributes as source-controlled code using GitLab CI and the Jamf Pro API.

Presented at [MacAdmins Conference 2026](https://psumac2026.sched.com/event/2NBpJ/jamf-as-code-managing-scripts-and-extension-attributes-with-gitlab-ci) — Jonathan Blasingame, Missouri University of Science and Technology.

---

## How It Works

Scripts and Extension Attributes live in this repository as plain text files. When changes merge to the `test` or `prod` branch, a GitLab CI pipeline runs `Pipeline/pipelineSync.py`, which reconciles the state of this repository against your Jamf Pro instance via the Jamf Pro API v1.

- **Git is the source of truth.** Jamf is the deployment target.
- **Full sync on every run.** Every pipeline run compares all files in this repo against all scripts/EAs in Jamf — not just what changed in the commit.
- **Git always wins.** If a script is edited directly in the Jamf UI, the next pipeline run will overwrite it.

---

## Repository Structure

```
jamf-as-code/
├── .gitlab-ci.yml                  # Pipeline definition
├── Pipeline/
│   ├── pipelineSync.py             # Sync orchestration
│   ├── JamfAPI.py                  # Jamf Pro API client
│   └── requirements.txt
├── Scripts/
│   └── <CategoryName>/             # Folder name = Jamf category
│       └── your-script.sh
└── ExtensionAttributes/
    └── <InventoryDisplayType>/     # Folder name = inventoryDisplayType
        └── Your EA Name.sh
```

### Scripts

Place script files under `Scripts/`, organized into subdirectories that match your Jamf category names.

- The **filename** (including extension) becomes the script name in Jamf Pro.
- The **parent folder name** maps to the Jamf category. If no matching category is found, the script is assigned to `None`.
- No shebang required — Jamf supplies the execution shell. Include one if you prefer.

```
Scripts/
├── Applications/
│   └── remove-adobe.sh       → name: "remove-adobe.sh", category: Applications
└── Security/
    └── check-filevault.sh    → name: "check-filevault.sh", category: Security
```

### Extension Attributes

Place EA scripts under `ExtensionAttributes/`, organized into subdirectories matching a valid Jamf `inventoryDisplayType`.

Valid types: `GENERAL`, `HARDWARE`, `OPERATING_SYSTEM`, `USER_AND_LOCATION`, `PURCHASING`, `EXTENSION_ATTRIBUTES`

EA metadata is declared in script header comments:

```bash
#!/bin/zsh
# EA-Description: A brief description of what this EA reports
# EA-DataType: STRING
```

- `EA-Description` — becomes the description field in Jamf Pro. Defaults to the file path if omitted.
- `EA-DataType` — `STRING`, `INTEGER`, or `DATE`. Defaults to `STRING` if omitted or invalid.
- The **filename stem** (filename without `.sh`) becomes the EA name in Jamf Pro. Spaces in filenames are supported.
- The **parent folder name** sets the `inventoryDisplayType`.

```
ExtensionAttributes/
├── HARDWARE/
│   └── Battery Condition.sh    → name: "Battery Condition", type: HARDWARE
└── GENERAL/
    └── Uptime Days.sh          → name: "Uptime Days", type: GENERAL
```

---

## Prerequisites

### Jamf Pro

1. **API Role** — Create a role with the following permissions only:
   - Read Scripts / Create Scripts / Update Scripts
   - Read Computer Extension Attributes / Create Computer Extension Attributes / Update Computer Extension Attributes

2. **API Client** — Create one client per environment (test + prod), assigned to the role above. Each client provides a `client_id` and `client_secret`.

### GitLab

3. **Protected branches** — Protect both `test` and `prod` branches (Settings → Repository → Branch Rules).
   - Merge: Maintainers only
   - Push: Administrators only

4. **CI/CD Variables** — Add the following as **Masked** and **Protected** variables (Settings → CI/CD → Variables):

   | Variable | Description |
   |---|---|
   | `JAMF_URL_PROD` | Your production Jamf URL, e.g. `https://yourinstance.jamfcloud.com/api` |
   | `JAMF_URL_TEST` | Your test Jamf URL |
   | `JAMF_CLIENT_ID_PROD` | API Client ID for production |
   | `JAMF_CLIENT_SECRET_PROD` | API Client secret for production |
   | `JAMF_CLIENT_ID_TEST` | API Client ID for test |
   | `JAMF_CLIENT_SECRET_TEST` | API Client secret for test |

---

## Branch Strategy

| Branch | Behavior |
|---|---|
| `main` | Work branch. Pipeline does **not** run here. |
| `test` | Protected. Deploys to your test Jamf instance. |
| `prod` | Protected. Deploys to production. Requires maintainer approval to merge. |

---

## Lifecycle Handling

| Event | Trigger | Behavior |
|---|---|---|
| **CREATE** | File in Git, no match in Jamf | `POST` — creates the object |
| **UPDATE** | File in both, content differs | `PUT` — full object replacement |
| **SOFT DELETE** | File removed from Git | Appends `__delete__=<timestamp+72h>` to the object's notes/description |
| **UNDELETE** | File restored before TTL expires | Deletion marker stripped on next sync |
| **HARD DELETE** | TTL has passed | Handled externally — see below |

### Soft Delete

When a script or EA is removed from Git, the pipeline does not immediately delete it from Jamf. Instead, it stamps the object with a deletion marker in the notes field:

```
__delete__=2026-07-10 14:23:07.454633+00:00
```

The object stays live in Jamf for 72 hours. This window exists because scripts can be referenced by policies — hard-deleting a script that a policy depends on causes that policy to fail silently at run time.

Executing the actual `DELETE` call after the TTL expires is left to a separate scheduled process. See [Script-Cleanup.py](https://github.com/mst-its/mstjamf) for the implementation we use.

**If you restore a removed file to Git before the TTL expires**, the pipeline strips the deletion marker on the next sync and the object is retained.

> **EA guardrail:** The cleanup process only deletes EAs where `inputType == SCRIPT`. EAs configured manually in the Jamf UI are never auto-deleted.

---

## Important: The Rename Warning

The pipeline tracks objects **by filename**. The filename in Git must match the script or EA name in Jamf Pro.

If you rename a file in Git without first renaming the object in Jamf, the pipeline will:
1. Create a new object with the new name
2. Stamp the old object for deletion

**Always rename in Jamf Pro first, then push the rename to Git.**

---

## Scaling to Multiple Environments

The URL-as-variable design makes adding new Jamf instances straightforward:

1. Create a new branch
2. Add `JAMF_URL_<ENV>`, `JAMF_CLIENT_ID_<ENV>`, and `JAMF_CLIENT_SECRET_<ENV>` as Protected CI/CD variables
3. Update `pipelineSync.py` `main()` to handle the new branch name

For MSPs or organizations managing multiple separate Jamf environments, we recommend **one repository per organization** rather than one repository with many branches. This keeps scripts isolated and prevents any risk of cross-environment contamination.

For shared or global scripts that need to reach multiple org repos, consider a central repository that opens merge requests to each org's repository when a shared script updates — the same pattern described in the talk's Installomator example.

---

## Acknowledgments

This project was presented at MacAdmins Conference 2026 with support from the [Mac Admins Foundation Charles S. Edge New Speaker Grant](https://www.macadmins.org).

The Jamf Pro API client (`JamfAPI.py`) and sync logic (`pipelineSync.py`) were built and are maintained by the IT department at Missouri University of Science and Technology.
