from .hub_swig import HubSwig


class DrcSwig(HubSwig):
    title = "Check manufacturability"
    description_text = "Check the board against CircuitNinja manufacturing constraints"
    entrypoint = "drc_ipc.py"
    icon = "drc.png"
    requires_project = True


class ExportSwig(HubSwig):
    title = "Export manufacturing files"
    description_text = "Check the board and export manufacturing files to a ZIP"
    entrypoint = "export_ipc.py"
    icon = "export.png"
    requires_project = True


class UploadSwig(HubSwig):
    title = "Upload to web app"
    description_text = (
        "Prepare and upload manufacturing files; log in from the menu first"
    )
    entrypoint = "upload_ipc.py"
    icon = "upload.png"
    requires_project = True
