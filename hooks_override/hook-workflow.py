# Override hook for the 'workflow' module.
# ACRPA has its own src/workflow.py which is NOT the third-party 'workflow' package.
# This empty hook prevents PyInstaller from trying to call copy_metadata('workflow')
# which would fail since the local module has no distribution metadata.

datas = []
hiddenimports = []
