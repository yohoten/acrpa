# Override hook for PIL.Image — prevents numpy from being pulled in.
# ACRPA uses Pillow for basic PNG/JPEG operations only (screenshot + QR code),
# and does NOT need numpy acceleration.
# The default PyInstaller hook calls collect_submodules('numpy') which
# adds ~20 MB of numpy C libraries (LAPACK/BLAS) to the build.

from PyInstaller.utils.hooks import collect_submodules

# Only collect PIL.Image submodules, NOT numpy
hiddenimports = collect_submodules('PIL.Image')
# Exclude all numpy-related hidden imports that PIL's default hook adds
excludedimports = ['numpy', 'numpy._core', 'numpy._globals']
