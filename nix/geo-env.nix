# GeoDjango loads these libraries with ctypes at import time and cannot find them
# in the store on its own, so both the dev shell and the wrappers set the paths.
{
  gdal,
  geos,
  proj,
  stdenv,
}:

let
  soExt = stdenv.hostPlatform.extensions.sharedLibrary;
in
{
  GDAL_LIBRARY_PATH = "${gdal}/lib/libgdal${soExt}";
  GEOS_LIBRARY_PATH = "${geos}/lib/libgeos_c${soExt}";
  PROJ_LIB = "${proj}/share/proj";
}
