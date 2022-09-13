import numba as nb

import functools



##### Types #####

scalar_types={  "u1":nb.u1, "u2":nb.u2, "u4":nb.u4, "u8":nb.u8,
                "i1":nb.i1, "i2":nb.i2, "i4":nb.i4, "i8":nb.i8,
                "f4":nb.f4, "f8":nb.f8}

@functools.lru_cache(1024)
def c_array(base="u1", ndim=1, readonly=False):
    """Generate a numba C-ordered array type with the given element type, number of dimensions, and read-only flag"""
    base=scalar_types.get(base,base)
    return nb.types.Array(base,ndim,"C",readonly=readonly)


##### Array access #####
_u1_d1_RC=c_array("u1",ndim=1,readonly=True)
@nb.njit(nb.u1(_u1_d1_RC,nb.u8))
def au1(x, off):
    """Extract a little-endian 1-byte unsigned integer from a numpy byte array at the given offset"""
    return x[off]
@nb.njit(nb.u2(_u1_d1_RC,nb.u8))
def au2(x, off):
    """Extract a little-endian 2-byte unsigned integer from a numpy byte array at the given offset"""
    return nb.u2((x[off+1]<<8)+x[off+0])
@nb.njit(nb.u4(_u1_d1_RC,nb.u8))
def au4(x, off):
    """Extract a little-endian 4-byte unsigned integer from a numpy byte array at the given offset"""
    return nb.u4((x[off+3]<<24)+(x[off+2]<<16)+(x[off+1]<<8)+x[off+0])
@nb.njit(nb.u8(_u1_d1_RC,nb.u8))
def au8(x, off):
    """Extract a little-endian 8-byte unsigned integer from a numpy byte array at the given offset"""
    return nb.u8((x[off+7]<<56)+(x[off+6]<<48)+(x[off+5]<<40)+(x[off+4]<<32)+(x[off+3]<<24)+(x[off+2]<<16)+(x[off+1]<<8)+x[off+0])

@nb.njit(nb.i1(_u1_d1_RC,nb.u8))
def ai1(x, off):
    """Extract a little-endian 1-byte unsigned integer from a numpy byte array at the given offset"""
    return nb.i1(x[off])
@nb.njit(nb.i2(_u1_d1_RC,nb.u8))
def ai2(x, off):
    """Extract a little-endian 2-byte unsigned integer from a numpy byte array at the given offset"""
    return nb.i2((x[off+1]<<8)+x[off+0])
@nb.njit(nb.i4(_u1_d1_RC,nb.u8))
def ai4(x, off):
    """Extract a little-endian 4-byte unsigned integer from a numpy byte array at the given offset"""
    return nb.i4((x[off+3]<<24)+(x[off+2]<<16)+(x[off+1]<<8)+x[off+0])
@nb.njit(nb.i8(_u1_d1_RC,nb.u8))
def ai8(x, off):
    """Extract a little-endian 8-byte unsigned integer from a numpy byte array at the given offset"""
    return nb.i8((x[off+7]<<56)+(x[off+6]<<48)+(x[off+5]<<40)+(x[off+4]<<32)+(x[off+3]<<24)+(x[off+2]<<16)+(x[off+1]<<8)+x[off+0])



##### Array tools #####

@functools.lru_cache(1024)
def copy_array_chunks(base="u1", par=False, nogil=True):
    """
    Generate and compile a numba function for copying an array in chunks.
    `base` specifies the base array type (by default, unsigned byte);
    if ``par==True``, generate a parallelized implementation.
    if ``nogil==True``, use the ``nogil`` numba option to release GIL during the execution.

    The returned function takes 4 arguments: source array, destination array, number of chunks, and size (in elements) of each chunk.
    """
    ain=c_array(base,readonly=True)
    aout=c_array(base,readonly=False)
    @nb.njit(nb.void(ain,aout,nb.u8,nb.u8),parallel=par,nogil=nogil)
    def copy(src, dst, n, size):
        for i in nb.prange(n):  # pylint: disable=not-an-iterable
            for p in range(size):
                dst[i*size+p]=src[i*size+p]
    return copy

@functools.lru_cache(1024)
def copy_array_strided(base="u1", par=False, nogil=True):
    """
    Generate and compile a numba function for copying an array in chunks with an arbitrary stride.
    `base` specifies the base array type (by default, unsigned byte);
    if ``par==True``, generate a parallelized implementation.
    if ``nogil==True``, use the ``nogil`` numba option to release GIL during the execution.

    The returned function takes 6 arguments: source array, destination array, number of chunks, size (in elements) of each chunk,
    chunks stride (in elements) in the source array, and offset (in elements) from the beginning of the first array.
    If size is the same as stride and the offset is zero, this function would mimic the one generated by :func:`copy_array_chunks`.
    """
    ain=c_array(base,readonly=True)
    aout=c_array(base,readonly=False)
    @nb.njit(nb.void(ain,aout,nb.u8,nb.u8,nb.u8,nb.u8),parallel=par,nogil=nogil)
    def copy_strided(src, dst, n, size, stride, off):
        for i in nb.prange(n):  # pylint: disable=not-an-iterable
            for p in range(size):
                dst[i*size+p]=src[i*stride+off+p]
    return copy_strided