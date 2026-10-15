from .hub_swig import HubSwig
from .manufacturing_constraints_swig import ManufacturingConstraintsSwig
from .workflow_swig import DrcSwig, ExportSwig, UploadSwig

HubSwig().register()
ManufacturingConstraintsSwig().register()
DrcSwig().register()
ExportSwig().register()
UploadSwig().register()
