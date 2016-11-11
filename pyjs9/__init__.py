from __future__ import print_function

import json
import base64

import requests
import six
from six import BytesIO

__all__ = ['JS9', 'js9Globals']

"""
pyjs9.py connects python and js9 via the js9Helper.js back-end server

- The JS9 class constructor connects to a single JS9 instance in a web page.
- The JS9 object supports the JS9 Public API and a shorter command-line syntax.
- See: http://js9.si.edu/js9/help/publicapi.html
- Send/retrieve numpy arrays and astropy (or pyfits) hdulists to/from js9.
- Use socketIO_client for fast, persistent connections to the JS9 back-end
"""

# pyjs9 version
__version__ = '1.4'

# try to be a little bit neat with global parameters
js9Globals = {}

js9Globals['version'] = __version__

# what sort of fits verification gets done on SetFITS() output?
# see astropy documentation on write method
js9Globals['output_verify'] = 'ignore'

# where to retrieve image data from JS9 as an array or as base64 encoded string
# js9Globals['retrieveAs'] = 'array'
js9Globals['retrieveAs'] = 'base64'

# load fits, if available
try:
    from astropy.io import fits
    js9Globals['fits'] = 1
except:
    try:
        import pyfits as fits
        if fits.__version__ >= '2.2':
            js9Globals['fits'] = 2
        else:
            js9Globals['fits'] = 0
    except:
        js9Globals['fits'] = 0

# load numpy, if available
try:
    import numpy
    js9Globals['numpy'] = 1
except:
    js9Globals['numpy'] = 0

# load socket.io, if available
try:
    from socketIO_client import SocketIO, LoggingNamespace
    js9Globals['transport'] = 'socketio'
    js9Globals['wait'] = 10
except:
    js9Globals['transport'] = 'html'
    js9Globals['wait'] = 0

# in python 3 strings are unicode
if six.PY3:
    unicode = str

# utilities
def _decode_list(data):
    rv = []
    for item in data:
        if six.PY2 and isinstance(item, unicode):
            item = item.encode('utf-8')
        elif isinstance(item, list):
            item = _decode_list(item)
        elif isinstance(item, dict):
            item = _decode_dict(item)
        rv.append(item)
    return rv

def _decode_dict(data):
    rv = {}
    for key, value in data.items():
        if six.PY2 and isinstance(key, unicode):
            key = key.encode('utf-8')
        if six.PY2 and isinstance(value, unicode):
            value = value.encode('utf-8')
        elif isinstance(value, list):
            value = _decode_list(value)
        elif isinstance(value, dict):
            value = _decode_dict(value)
        rv[key] = value
    return rv

# numpy-dependent routines
if js9Globals['numpy']:
    def _bp2np(bitpix):
        """
        Convert FITS bitpix to numpy datatype
        """
        if bitpix == 8:
            return numpy.uint8
        elif bitpix == 16:
            return numpy.int16
        elif bitpix == 32:
            return numpy.int32
        elif bitpix == 64:
            return numpy.int64
        elif bitpix == -32:
            return numpy.float32
        elif bitpix == -64:
            return numpy.float64
        elif bitpix == -16:
            return numpy.uint16
        else:
            raise ValueError('unsupported bitpix: %d' % bitpix)

    def _np2bp(dtype):
        """
        Convert numpy datatype to FITS bitpix
        """
        if dtype == numpy.uint8:
            return 8
        elif dtype == numpy.int16:
            return 16
        elif dtype == numpy.int32:
            return 32
        elif dtype == numpy.int64:
            return 64
        elif dtype == numpy.float32:
            return -32
        elif dtype == numpy.float64:
            return -64
        elif dtype == numpy.uint16:
            return -16
        else:
            raise ValueError('unsupported dtype: %s' % dtype)

    def _bp2py(bitpix):
        """
        Convert FITS bitpix to python datatype
        """
        if bitpix == 8:
            return 'B'
        elif bitpix == 16:
            return 'h'
        elif bitpix == 32:
            return 'l'
        elif bitpix == 64:
            return 'q'
        elif bitpix == -32:
            return 'f'
        elif bitpix == -64:
            return 'd'
        elif bitpix == -16:
            return 'H'
        else:
            raise ValueError('unsupported bitpix: %d' % bitpix)

    def _im2np(im):
        """
        Convert GetImageData object to numpy
        """
        w = int(im['width'])
        h = int(im['height'])
        d = 1
        bp = int(im['bitpix'])
        dtype = _bp2np(bp)
        dlen = h * w * abs(bp) // 8
        if js9Globals['retrieveAs'] == 'array':
            s = im['data'][0:h*w]
            if d > 1:
                arr = numpy.array(s, dtype=dtype).reshape((d, h, w))
            else:
                arr = numpy.array(s, dtype=dtype).reshape((h, w))
        elif js9Globals['retrieveAs'] == 'base64':
            if six.PY3:
                im_data = im['data'].encode()
            else:
                im_data = im['data']
            s = base64.decodestring(im_data)[0:dlen]
            if d > 1:
                arr = numpy.frombuffer(s, dtype=dtype).reshape((d, h, w))
            else:
                arr = numpy.frombuffer(s, dtype=dtype).reshape((h, w))
        else:
            raise ValueError('unknown retrieveAs type for GetImageData()')
        return arr

class JS9(object):
    """
    The JS9 class supports communication with an instance of JS9 in a web
    page, utilizing the JS9 Public API calls as class methods.

    JS9's public access library is documented here:

    - http://js9.si.edu/js9/help/publicapi.html

    In addition, a number of special methods are implemented to facilitate data
    access to/from well-known Python objects:

    - GetNumpy: retrieve a FITS image or an array into a numpy array
    - SetNumpy: send a numpy array to JS9 for display
    - GetFITS: retrieve a FITS image into an astropy (or pyfits) HDU list
    - SetFITS: send a astropy (or pyfits) HDU list to JS9 for display

    """

    def __init__(self, host='http://localhost:2718', id='JS9'):
        """
        :param host: host[:port] (default is 'http://localhost:2718')
        :param id: the JS9 display id (default is 'JS9')

        :rtype: JS9 object connected to a single instance of js9

        The JS9() contructor takes its first argument to be the host (and
        optional port) on which the back-end js9Helper is running. The default
        is 'http://localhost:2718', which generally will be the correct value
        for running locally. The default port (2718) will be added if no port
        value is found. The string 'http://' will be prefixed to the host if a
        URL protocol is not supplied. Thus, to connect to the main JS9 web
        site, you can use host='js9.si.edu'.

        The second argument is a JS9 display id on the web page. The
        default is 'JS9' which is the default JS9 display id. Thus:

          >>> JS9 = pyjs9.JS9()

        is appropriate for local web pages having only one JS9 display.
        """
        self.__dict__['id'] = id
        # add default port, if necessary
        c = host.rfind(':')
        s = host.find('/')
        if(c <= s):
            host += ':2718'
        if(s < 0):
            host = 'http://' + host
        self.__dict__['host'] = host
        # open socket.io connection, if necessary
        if js9Globals['transport'] == 'socketio':
            try:
                a = host.rsplit(':',1)
                self.sockio = SocketIO(a[0], int(a[1]))
            except:
                js9Globals['transport'] = 'html'
        self._alive()

    def __setitem__(self, itemname, value):
        """
        An internal routine to process some assignments specially
        """
        self.__dict__[itemname] = value
        if itemname == 'host' or itemname == 'id':
            self._alive()

    def _alive(self):
        """
        An internal routine to send a test message to the helper
        """
        self.send(None, msg='alive')

    def sockioCB(self, *args):
        self.__dict__['sockioResult'] = args[0]

    def send(self, obj, msg='msg'):
        """
        :obj: dictionary containing command and args keys

        :rtype: returned data or info (in format specified by public api)

        examples:
        >>> js9 = pyjs9.JS9()
        >>> js9.send({'cmd': 'GetColormap'})
        {u'bias': 0.5, u'colormap': u'cool', u'contrast': 1}
        >>> js9.send({'cmd': 'SetColormap', 'args': ['red']})
        'OK'
        """
        if obj is None:
            obj = {}
        obj['id'] = self.id

        if js9Globals['transport'] == 'html':
            jstr = json.dumps(obj)
            try:
                url = requests.get(self.host + '/' + msg, params=jstr)
            except IOError as e:
                raise IOError('Cannot connect to {0}: {1}'.format(self.host,
                                                                  e.strerror))
            urtn = url.text
            if 'ERROR:' in urtn:
                raise ValueError(urtn)
            try:
                # TODO: url.json() decode the json for us:
                # http://www.python-requests.org/en/latest/user/quickstart/#json-response-content
                # res = url.json()
                res = json.loads(urtn, object_hook=_decode_dict)
            except ValueError:   # not json
                res = urtn
            return res
        else:
            self.__dict__['sockioResult'] = ''
            self.sockio.emit('msg', obj, self.sockioCB)
            self.sockio.wait_for_callbacks(seconds=js9Globals['wait'])
            if self.__dict__['sockioResult'] and isinstance(self.__dict__['sockioResult'], str) and 'ERROR:' in self.__dict__['sockioResult']:
                raise ValueError(self.__dict__['sockioResult'])
            return self.__dict__['sockioResult']

    if js9Globals['fits']:
        def GetFITS(self):
            """
            :rtype: fits hdulist

            To read FITS data or a raw array from js9 into fits, use the
            'GetFITS' method. It takes no args and returns an hdu list::

              >>> hdul = d.GetFITS()
              >>> hdul.info()
              Filename: StringIO.StringIO
              No.    Name         Type      Cards   Dimensions   Format
              0    PRIMARY     PrimaryHDU      24  (1024, 1024)  float32
              >>> data = hdul[0].data
              >>> data.shape
              (1024, 1024)

            """
            # get image data from JS9
            im = self.GetImageData(js9Globals['retrieveAs'])
            # convert to numpy
            arr = _im2np(im)
            # add fits cards
            # create FITS primary hdu from numpy array
            hdu = fits.PrimaryHDU(arr)
            hdulist = fits.HDUList([hdu])
            return hdulist

        def SetFITS(self, hdul, name=None):
            """
            :param hdul: fits hdulist
            :param name: fits file or object name (used as id)

            After manipulating or otherwise modifying a fits hdulist (or
            making a new one), you can display it in js9 using the 'SetFITS'
            method, which takes the hdulist as its sole argument::

              >>> j.SetFITS(nhdul)

            Note that this routine creates a new image in the JS9 display. If
            you want to update the current image, use RefreshImage. In that
            case, the hdul's numpy array must be converted to a list:

              >>>> j.RefreshImage(hdul[0].data.tolist())
            """
            if not js9Globals['fits']:
                raise ValueError('SetFITS not defined (fits not found)')
            if type(hdul) != fits.HDUList:
                if js9Globals['fits'] == 1:
                    raise ValueError('requires astropy.HDUList as input')
                else:
                    raise ValueError('requires pyfits.HDUList as input')
            # in-memory string
            memstr = BytesIO()
            # write fits to memory string
            hdul.writeto(memstr, output_verify=js9Globals['output_verify'])
            # get memory string as an encoded string
            encstr = base64.b64encode(memstr.getvalue())
            # set up JS9 options
            opts = {}
            if name:
                opts['filename'] = name
            # send encoded file to JS9 for display
            got = self.Load(encstr, opts)
            # finished with memory string
            memstr.close()
            return got

    else:
        def GetFITS(self):
            """
            This method is not defined because fits in not installed.
            """
            raise ValueError('GetFITS not defined (astropy.io.fits not found)')

        def SetFITS(self):
            """
            This method is not defined because fits in not installed.
            """
            raise ValueError('SetFITS not defined (astropy.io.fits not found)')

    if js9Globals['numpy']:
        def GetNumpy(self):
            """
            :rtype: numpy array

            To read a FITS file or an array from js9 into a numpy array, use
            the 'GetNumpy' method. It takes no arguments and returns the
            np array::

              >>> j.get('file')
              '/home/eric/data/casa.fits[EVENTS]'
              >>> arr = d.GetNumpy()
              >>> arr.shape
              (1024, 1024)
              >>> arr.dtype
              dtype('float32')
              >>> arr.max()
              51.0
            """
            # get image data from JS9
            im = self.GetImageData(js9Globals['retrieveAs'])
            # convert to numpy
            arr = _im2np(im)
            return arr

        def SetNumpy(self, arr, filename=None, dtype=None):
            """
            :param arr: numpy array
            :param name: file or object name (used as id)
            :param dtype: data type into which to convert array before sending

            After manipulating or otherwise modifying a numpy array (or making
            a new one), you can display it in js9 using the 'SetNumpy' method,
            which takes the array as its first argument::

              >>> j.SetNumpy(arr)

            An optional second argument specifies a datatype into which the
            array will be converted before being sent to js9. This is
            important in the case where the array has datatype np.uint64,
            which is not recognized by js9::

              >>> j.SetNumpy(arru64)
              ...
              ValueError: uint64 is unsupported by JS9 (or FITS)
              >>> j.SetNumpy(arru64,dtype=np.float64)

            Also note that np.int8 is sent to js9 as int16 data, np.uint32 is
            sent as int64 data, and np.float16 is sent as float32 data.

            Note that this routine creates a new image in the JS9 display. If
            you want to update the current image, use RefreshImage. In that
            case, the numpy array must be converted to a list:

              >>>> j.RefreshImage(arr.tolist())
            """
            if type(arr) != numpy.ndarray:
                raise ValueError('requires numpy.ndarray as input')
            if dtype and dtype != arr.dtype:
                narr = arr.astype(dtype)
            else:
                if arr.dtype == numpy.int8:
                    narr = arr.astype(numpy.int16)
                elif arr.dtype == numpy.uint32:
                    narr = arr.astype(numpy.int64)
                elif hasattr(numpy, 'float16') and arr.dtype == numpy.float16:
                    narr = arr.astype(numpy.float32)
                else:
                    narr = arr
            if not narr.flags['C_CONTIGUOUS']:
                narr = numpy.ascontiguousarray(narr)
            # parameters to pass back to JS9
            bp = _np2bp(narr.dtype)
            (h, w) = narr.shape
            dmin = narr.min().tolist()
            dmax = narr.max().tolist()
            # base64-encode numpy array in native format
            encarr = base64.b64encode(narr.tostring())
            # create object to send to JS9 containing encoded array
            hdu = {'naxis': 2, 'naxis1': w, 'naxis2': h, 'bitpix': bp,
                   'dmin': dmin, 'dmax': dmax, 'encoding': 'base64',
                   'image': encarr}
            if filename:
                hdu['filename'] = filename
            # send encoded file to JS9 for display
            return self.Load(hdu)

    else:
        def GetNumpy(self):
            """
            This method is not defined because numpy in not installed.
            """
            raise ValueError('GetNumpy not defined (numpy not found)')

        def SetNumpy(self):
            """
            This method is not defined because numpy in not installed.
            """
            raise ValueError('SetNumpy not defined (numpy not found)')

    def Load(self, *args):
        """
        Load an image into JS9

        call:

        Load(url, opts)

        where:

        -  url: url, fits object, or in-memory FITS
        -  opts: object containing image parameters

        NB: In Python, you probably want to call JS9.SetFITS() or
        JS9.SetNumpy() to load a local file into JS9.

        Load a FITS file or a PNG representation file into JS9. Note that
        a relative URL is relative to the JS9 install directory.

        You also can pass an in-memory buffer containing a FITS file, or a
        string containing a base64-encoded FITS file.

        Finally, you can pass a fits object containing the following
        properties:

        -  naxis: number of axes in the image
        -  axis: array of image dimensions for each axis or ...
        -  naxis[n] image dimensions of each axis (naxis1, naxis2, ...)
        -  bitpix: FITS bitpix value
        -  head: object containing header keywords as properties
        -  image: list containing image pixels
        -  dmin: data min (optional)
        -  dmax: data max (optional)

        To override default image parameters, pass the image opts argument:

            >>> j.Load('png/m13.png', {'scale':'linear', 'colormap':'sls'})
        """
        return self.send({'cmd': 'Load', 'args': args})

    def LoadProxy(self, *args):
        """
        Load an FITS image link into JS9 using a proxy server

        call:

        LoadProxy(url, opts)

        where:

        -  url: remote URL link to load
        -  opts: object containing image parameters

        Load a FITS file specified by an arbitrary URL into JS9 using
        the JS9 back-end helper as a proxy server. Not all back-end
        servers support the proxy functionality.  The main JS9 web
        site does support proxy service, and can be used to view
        images from arbitrary URLs.

        The JS9.LoadProxy() call takes a URL as its first argument.
        This URL will be retrieved using curl or wget and stored on the
        back-end server in a directory specifically tied to the web page.
        (The directory and its contents will be deleted when the page is
        unloaded.) JS9 then will load the file from this directory.
        Note that since the file resides on the back-end server, all
        back-end analysis defined on that server is available.

        To override default image parameters, pass the image opts argument:

          >>> j.LoadProxy('http://hea-www.cfa.harvard.edu/~eric/coma.fits', {'scale':'linear', 'colormap':'sls'})

        If an onload callback function is specified in opts, it will be called
        after the image is loaded:

          >>> j.LoadProxy('http://hea-www.cfa.harvard.edu/~eric/coma.fits', {'scale': 'linear', 'onload': func})
        """
        return self.send({'cmd': 'LoadProxy', 'args': args})

    def GetLoadStatus(self, *args):
        """
        Get Load Status

        call:

        status  = GetLoadStatus(id)

        where:

        -  id: the id of the file that was loaded into JS9

        returns:

        -  status: status of the load

        This routine returns the status of the load process for this image.
        It is needed in certain cases where JS9.Load() returns before the image
        data is actially loaded into the display.

        A status of 'complete' means that the image is fully loaded. Other
        statuses include:

        -  loading: the image is in process of loading
        -  error: image did not load due to an error
        -  other: another image is loaded into this display
        -  none: no image is loaded into this display
        """
        return self.send({'cmd': 'GetLoadStatus', 'args': args})

    def RefreshImage(self, *args):
        """
        Re-read the image data and re-display

        call:

        RefreshImage(input)

        where:

        -  input: python list

        This routine can be used, for example, in laboratory settings where
        data is being gathered in real-time and the JS9 display needs to be
        refreshed periodically. The first input argument can be one of the
        following:

        -  a list containing image pixels (for numpy, use tolist() to convert)
        -  a two-dimensional list containing image pixels
        -  a dictionary containing a required image property and any of the
           following optional properties:

           -  naxis: number of axes in the image
           -  axis: array of image dimensions for each axis or ...
           -  naxis[n] image dimensions of each axis (naxis1, naxis2, ...)
           -  bitpix: FITS bitpix value
           -  head: object containing header keywords as properties
           -  dmin: data min (optional)
           -  dmax: data max (optional)

        When passing an object as input, the required image property that
        contains the image data can be a list or a list of lists containing
        data. It also can contain a base64-encoded string containing a list.
        This latter can be useful when calling JS9.RefreshImage() via HTTP.
        Ordinarily, when refreshing an image, there is no need to specify the
        optional axis, bitpix, or header properties. But note that you actually
        can change these values on the fly, and JS9 will process the new data
        correctly. Also, if you do not pass dmin or dmax, they will be
        calculated by JS9.

        Note that you can pass a complete FITS file to this routine. It will be
        passed to the underlying FITS-handler before being displayed.  Thus,
        processing time is slightly greater than if you pass the image data
        directly.

        The main difference between JS9.RefreshImage() and JS9.Load() is
        that the former updates the data into an existing image, while the
        latter adds a completely new image to the display.
        """
        return self.send({'cmd': 'RefreshImage', 'args': args})

    def CloseImage(self, *args):
        """
        Clear the image from the display and mark resources for release

        call:

        CloseImage()

        Each loaded image claims a non-trivial amount of memory from a finite
        amount of browser heap space. For example, the default 32-bit version
        of Google Chrome has a memory limit of approximately 500Mb. If you are
        finished viewing an image, closing it tells the browser that the
        image's memory can be freed. In principle, this is can help reduce
        overall memory usage as successive images are loaded and discarded.
        Note, however, that closing an image only provides a hint to the
        browser, since this sort of garbage collection is not directly
        accessible to JavaScript programming.

        Some day, all browsers will support full 64-bit addressing and this
        problem will go away ...
        """
        return self.send({'cmd': 'CloseImage', 'args': args})

    def GetImageData(self, *args):
        """Get image data and auxiliary info for the specified image

        call:

        imdata  = GetImageData(dflag)

        where:

        -  dflag: specifies whether the data should also be returned

        returns:

        -  imdata: image data object

        NB: In Python, you probably want to call JS9.GetFITS() or
        JS9.GetNumpy() to retrieve an image.

        The image data object contains the following information:

        -  id: the id of the file that was loaded into JS9
        -  file: the file or URL that was loaded into JS9
        -  fits: the FITS file associated with this image
        -  source: 'fits' if a FITS file was downloaded, 'fits2png' if a
           representation file was retrieved
        -  imtab: 'image' for FITS images and png files, 'table' for FITS
           binary tables
        -  width: x dimension of image
        -  height: y dimension of image
        -  bitpix: FITS bits/pixel of each image element (8 for unsigned
           char, 16, 32 for signed integer, -32 or -64 for float)
        -  header: object containing FITS header values
        -  data: buffer containing raw data values

        This call can return raw data for subsequent use in local analysis
        tasks. The format of the returned data depends on the exact value of
        dflag. If dflag is the boolean value true, an HTML5 typed array
        is returned, which translates into a dictionary of pixels values in
        Python. While typed arrays are more efficient than ordinary JavaScript
        arrays, this is almost certainly not what you want in Python.

        If dflag is the string 'array', a Python list of pixel values is
        returned. Intuitively, this would seem to what is wanted, but ... it
        appears that base64-encoded strings are transferred more quickly
        through the JS9 helper than are binary data.

        If dflag is the string 'base64', a base64-encoded string is returned.
        Oddly, this seems to be the fastest method of transferring
        data via socket.io to an external process such as Python, and, in
        fact, is the method used by the pyjs9 numpy and fits routines.

        The file value can be a FITS file or a representation PNG file. The
        fits value will be the path of the FITS file associated with this
        image. For a presentation PNG file, the path generally will be relative
        to the JS9 install directory. For a normal FITS file, the path usually
        is an absolute path to the FITS file.
        """
        return self.send({'cmd': 'GetImageData', 'args': args})

    def GetDisplayData(self, *args):
        """
        Get image data for all images loaded into the specified display

        call:

        imarr = JS9.GetDisplayData()

        returns:

        - imarr: array of image data objects

        The JS9.GetDisplayData() routine returns an array of image data
        objects, one for each images loaded into the specified display.
        That is, it returns the same type of information as JS9.GetImageData(),
        but does so for each image associated with the display, not just the
        current image.
        """
        return self.send({'cmd': 'GetDisplayData', 'args': args})

    def DisplayExtension(self, *args):
        """
        Display an extension from a multi-extension FITS file

        call:

        JS9.DisplayExtension(extid, opts)

        where:

        - extid: HDU extension number or the HDU's EXTNAME value
        - opts: object containing options

        This routine allows you to display images and even binary
        tables from a multi-extension FITS file. (See, for example,
        http://fits.gsfc.nasa.gov/fits_primer.htmlthe FITS Primer
        for information about HDUs and multi-extension FITS).
        """
        return self.send({'cmd': 'DisplayExtension', 'args': args})

    def DisplaySlice(self, *args):
        """
        Display a slice of a FITS data cube

        call:

        JS9.DisplaySlice(slice, opts)

        where:

        - slice: slice description or slice number
        - opts: object containing options

        This routine allows you to display a 2D slice of a 3D or 4D
        FITS data cube, i.e.  a FITS image containing 3 or 4 axes.

        The slice parameter can either be the numeric value of the
        slice in the third (or fourth) image dimension (starting
        with 1) or it can be a slice description string: a combination
        of asterisks and a numeric value defines the slice axis. Thus, for
        example, in a 1024 x 1024 x 16 cube, you can display the sixth slice
        along the third axis in one of two ways:

          >>> JS9.DisplaySlice(6)

        or:

          >>> JS9.DisplaySlice("*,*,6")

        If the image was organized as 16 x 1024 x 1024, you would use the
        string description:

          >>> JS9.DisplaySlice("6,*,*")

        By default, the new slice replaces the data in the currently displayed
        image. You can display the slice as a separate image by supplying
        an opts object with its separate property set to true.
        For example:

          >>> JS9.DisplaySlice("6,*,*", {separate: true})

        will display the sixth slice of the first image dimension separately
        from the original file, allowing blinking, image blending, etc. between
        the two "files".  Note that the new id and filename are adjusted to be
        the original file's values with the cfitsio image section [6:6,*,*]
        appended.
        """
        return self.send({'cmd': 'DisplaySlice', 'args': args})

    def MoveToDisplay(self, *args):
        """
        Move an image to a new JS9 display

        call:

        JS9.MoveToDisplay(dname)

        where:

        - dname: name of JS9 display to which the current image will be moved

        The JS9.MoveToDisplay() routine moves the current image to the
        specified display:

          >>> JS9.MoveToDisplay("myJS9")

        will move the current image displayed in the "JS9" display window to
        the "myJS9" window.

        Note that the new JS9 display must already exist. New displays can be
        created with the JS9.LoadWindow() public access routine or
        the File:new JS9 light window menu option.
        """
        return self.send({'cmd': 'MoveToDisplay', 'args': args})

    def BlendImage(self, *args):
        """
        Blend the image in an image stack using W3C composite/blend modes

        call:

        JS9.BlendImage(blendMode, opacity)

        Calling sequences:

        JS9.BlendImage()                   # return current blend params
        JS9.BlendImage(true||false)        # turn on/off blending
        JS9.BlendImage(mode, opacity)      # set blend mode and/or opacity

        where:

        - mode: one of the W3C bend modes
        - opacity: the opacity of the blended image (percent from 0 to 1)

        Image processing programs such as Adobe Photoshop and Gimp allow you
        to blend a stack of images together by mixing the RGB colors. The W3C
        has defined a number of composite and blending modes which have been
        implemented by Firefox, Chrome, and Safari (what about IE?):
        - normal
        - multiply
        - screen
        - overlay
        - darken
        - lighten
        - color-dodge
        - color-burn
        - hard-light
        - soft-light
        - difference
        - exclusion
        - hue
        - saturation
        - color
        - luminosity

        In addition, the following Porter-Duff compositing modes are available
        (though its unclear how useful they are in JS9 image processing):

        - clear
        - copy
        - source-over
        - destination-over
        - source-in
        - destination-in
        - source-out
        - destination-out
        - source-atop
        - destination-atop
        - xor
        - lighter

        Blending and compositing modes are described in detail in:

        https://www.w3.org/TR/compositing-1
        https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API/Tutorial/Compositing

        JS9 allows you to use these modes to blend images together. If
        you load two images of the same object into JS9, you can use
        the JS9.ReprojectData() routine to align them by WCS. You then
        can blend one image into the other by specifying a blend mode
        and an optional opacity. For example, if chandra.fits and
        spitzer.fits are two aligned images of the same object, and
        chandra.fits is currently being displayed, you can blend
        spitzer into chandra using the "screen" blend and opacity 0.9
        mode this way:

              >>> JS9.BlendImage("screen", 0.9)

        After the spitzer image is blended, both images will be
        displayed as part of the chandra.fits display. However,
        changing the colormap, scale, contrast, or bias will only
        affect the current chandra image, not the blended spitzer
        part.  In this way, you can continue to manipulate the current
        image and the image blending will update automatically.

        Also note that the spitzer image is still available separately
        for display and manipulation. You can switch to displaying
        spitzer and change colormap, scale, bias, contrast, etc. But
        since the images are now blended, changes to spitzer will be
        reflected in the spitzer part of the blended chandra
        display. Thus, if you change the colormap on the display of
        spitzer, and change back to chandra, the blended chandra image
        will utilize the new colormap.

        This linkage is maintained during zoom and pan operations. If
        you display the blended chandra image and then zoom or pan it,
        both images will be updated correctly to maintain
        alignment. But note that this means when you go back to the
        spitzer display, its zoom and/or pan values will have been
        updated. In this way, the spitzer image always is correctly
        linked to the blended version.

        The JS9.BlendImage() call accepts a variable number of
        arguments to perform a variety of functions:
        JS9.BlendImage() returns an object containing the following properties:

        - active: boolean specifying whether this image is to be blended
        - mode: string specifying the blend mode
        - opacity: opacity value (0 to 1)

          >>> JS9.BlendImage()   # returns a blend object for the current image
          >>> JS9.BlendImage(true||false) # turns on/off blending of
          >>> JS9.BlendImage(blend, opacity) # set or modify the blend mode or opacity
        """
        return self.send({'cmd': 'BlendImage', 'args': args})

    def GetColormap(self, *args):
        """
        Get the image colormap

        call:

        cmap  = GetColormap()

        returns:

        -  cmap: an object containing colormap information.

        The returned cmap object will contain the following properties:

        -  colormap: colormap name
        -  contrast: contrast value (range: 0 to 10)
        -  bias: bias value (range 0 to 1)
        """
        return self.send({'cmd': 'GetColormap', 'args': args})

    def SetColormap(self, *args):
        """
        Set the image colormap

        call:

        SetColormap(cmap, [contrast, bias])

        where:

        -  cmap: colormap name
        -  contrast: contrast value (range: 0 to 10)
        -  bias: bias value (range 0 to 1)

        Set the current colormap, contrast/bias, or both. This call takes one
        (colormap), two (contrast, bias) or three (colormap, contrast, bias)
        arguments.
        """
        return self.send({'cmd': 'SetColormap', 'args': args})

    def SaveColormap(self, *args):
        """
        Save current colormap definition

        call:

        JS9.SaveColormap(filename)

        where:

        - filename: output file name

        Save the current colormap definition for displayed image as a
        json file. If filename is not specified, the file will be
        saved as "js9.cmap".  This is useful if you want to edit the
        current definition to make a new colormap.

        Don't forget that the file is saved by the browser, in whatever
        location you have set up for downloads.
        """
        return self.send({'cmd': 'SaveColormap', 'args': args})

    def AddColormap(self, *args):
        """
        Add a colormap to JS9

        call:

        JS9.AddColormap(name, aa|rr,gg,bb|obj|json)

        where:

        - name: colormap name
        - aa: an array containing RGB color triplets
        - rr,gg,bb: 3 arrays of vertices specifying color changes
        - obj: object containing one of the two colormap definition formats
        - json: json string containing one of the colormap definition formats

        You can add new colormaps to JS9 using one of two formats. The
        first is an array of RGB triplets (i.e. an array of 3-D
        arrays), where each triplet defines a color. The elements of
        the colormap are divided evenly between these 3-D triplets.
        For example, the i8 colormap is defined as:

          >>> JS9.AddColormap("i8", [[0,0,0], [0,1,0], [0,0,1], [0,1,1], [1,0,0], [1,1,0], [1,0,1], [1,1,1]]))

        Here, the colormap is divided into 8 sections having the
        following colors: black, green, blue, cyan (green + blue),
        red, yellow (red + green), purple (red + blue), and white. A
        colormap such as sls also utilizes an array of RGB triplets,
        but it has 200 entries, leading to much more gradual
        transitions between colors.

        The second colormap format consists three arrays of vertices
        defining the change in intensity of red, green, and blue,
        respectively. For each of these three color triplets, the
        first coordinate of each vertex is the x-distance along the
        colormap axis (scaled from 0 to 1) and the second coordinate
        is the y-intensity of the color.  Colors are interpolated
        between the vertices.  For example, consider the following:

          >>> JS9.AddColormap("red",[[0,0],[1,1]], [[0,0], [0,0]], [[0,0],[0,0]])
          >>> JS9.AddColormap("blue",[[0,0],[0,0]], [[0,0], [0,0]], [[0,0],[1,1]])
          >>> JS9.AddColormap("purple", [[0,0],[1,1]], [[0,0], [0,0]],[[0,0],[1,1]])

        In the red (blue) colormap, the red (blue) array contains two
        vertices, whose color ranges from no intensity (0) to full
        intensity (1) over the whole range of the colormap (0 to
        1). The same holds true for the purple colormap, except that
        both red and blue change from zero to full intensity.

        For a more complicated example, consider the a colormap, which is
        defined as:

          >>> JS9.AddColormap("a", [[0,0], [0.25,0], [0.5,1], [1,1]], [[0,0], [0.25,1], [0.5,0], [0.77,0], [1,1]], [[0,0], [0.125,0], [0.5, 1], [0.64,0.5], [0.77, 0], [1,0]])

        Here we see that red is absent for the first quarter of the
        colormap, then gradually increases to full intensity by the
        half mark, after which it stays at full intensity to the
        end. Green ramps up to full intensity in the first quarter,
        then drops to zero by the half and stays that way until a bit
        more than three-quarters along, after which it gradually
        increases again. Blue starts off at no intensity for an
        eighth, then gradually increases to full intensity by the
        half-way mark, decreasing gradually to zero by the
        three-quarter mark. The result is that you see, for example,
        green at the beginning and yellow (red + green) at the end,
        with some purple (red + blue) in the middle of the colormap.

        As a convenience, you also can pass an object or json string
        containing the colormap definition:

        # RGB color triplets for the I8 colormap in a "colors" property
        {"name":"i8","colors":[[0,0,0],[0,1,0],[0,0,1],[0,1,1],[1,0,0],[1,1,0],[1,0,1],[1,1,1]]}

        # all 3 vertex arrays for the purple colormap in one "vertices" property
        {"name":"purple","vertices":[[[0,0],[1,1]],[[0,0],[0,0]],[[0,0],[1,1]]]}

        Finally, note that JS9.AddColormap() adds its new colormap to
        all JS9 displays on the given page.
        """
        return self.send({'cmd': 'AddColormap', 'args': args})

    def GetRGBMode(self, *args):
        """
        Get RGB mode information

        call:

        rgbobj  = GetRGBMode()

        returns:

        - rgbobj: RGB mode information

        This routine returns an object containing the following RGB mode
        information:

        - active: boolean specifying whether RGB mode is active
        - rid: image id of "red" image
        - gid: image id of "green" image
        - bid: image id of "blue" image
        """
        return self.send({'cmd': 'GetRGBMode', 'args': args})

    def SetRGBMode(self, *args):
        """
        call:

        SetRGBMode(mode, [imobj])

        where:

        - mode: boolean true to activate RGB mode, false to disable
        - imobj: optional object specifying three images to set to the
          "red", "green", and "blue" colormaps

        In RGB mode, three images assigned the "red", "green", and "blue"
        colormaps are displayed as a single image. The RGB color of each
        displayed pixel is a combination of the "red", "green", and "blue"
        pixel value taken from the appropriate image. Note that all three
        images are not required: you can display an RGB image using two of
        the three colors simply by not assigning the third colormap.

        The SetRGBMode() call turns on or off RGB mode. The
        boolean mode argument specifies whether to activate or
        de-activate RGB mode. The optional imobj object specifies
        (already-loaded) images to assign to the three colormaps:

        - rid: image id (or handle) to set to the "red" colormap
        - gid: image id (or handle) to set to the "green" colormap
        - bid: image id (or handle) to set to the "blue" colormap

        If imobj is not specified, it is assumed that images have been
        assigned the "red", "green", and "blue" colormaps by another means.
        (Once again, it is not necessary to assign all three colormaps.)

        """
        return self.send({'cmd': 'SetRGBMode', 'args': args})

    def GetZoom(self, *args):
        """
        Get the image zoom factor

        call:

        zoom  = GetZoom()

        returns:

        -  zoom: floating point zoom factor
        """
        return self.send({'cmd': 'GetZoom', 'args': args})

    def SetZoom(self, *args):
        """
        Set the image zoom factor

        call:

        SetZoom(zoom)

        where:

        -  zoom: floating or integer zoom factor or zoom directive string

        The zoom directives are:

        -  x[n]\|X[n]: multiply the zoom by n (e.g. 'x2')
        -  /[n]: divide the zoom by n (e.g. '/2')
        -  in\|In: zoom in by a factor of two
        -  out\|Out: zoom out by a factor of two
        -  toFit\|ToFit: zoom to fit image in display
        """
        return self.send({'cmd': 'SetZoom', 'args': args})

    def GetPan(self, *args):
        """
        Get the image pan position

        call:

        ipos  = GetPan()

        returns:

        -  ipos: object containing image information for pan

        The returned ipos object will contain the following properties:

        -  x: x image coordinate of center
        -  y: y image coordinate of center
        """
        return self.send({'cmd': 'GetPan', 'args': args})

    def SetPan(self, *args):
        """
        Set the image pan position

        call:

        SetPan(x, y)

        where:

        -  x: x image coordinate
        -  y: y image coordinate

        Set the current pan position using image coordinates. Note that you can
        use JS9.WCSToPix() and JS9.PixToWCS() to convert between image
        and WCS coordinates.
        """
        return self.send({'cmd': 'SetPan', 'args': args})

    def GetScale(self, *args):
        """
        Get the image scale

        call:

        scale  = GetScale()

        returns:

        -  scale: object containing scale information

        The returned scale object will contain the following properties:

        -  scale: scale name
        -  scalemin: min value for scaling
        -  scalemax: max value for scaling
        """
        return self.send({'cmd': 'GetScale', 'args': args})

    def SetScale(self, *args):
        """
        Set the image scale

        call:

        SetScale(scale, smin, smax)

        where:

        -  scale: scale name
        -  smin: scale min value
        -  smax: scale max value

        Set the current scale, min/max, or both. This call takes one (scale),
        two (smin, max) or three (scale, smin, smax) arguments.
        """
        return self.send({'cmd': 'SetScale', 'args': args})

    def GetValPos(self, *args):
        """
        Get value/position information

        call:

        valpos  = GetValPos(ipos)

        where:

        -  ipos: image position object containing x and y image coord values

        returns:

        -  valpos: value/position object

        This routine determines the data value at a given image position and
        returns an object containing the following information:

        -  ix: image x coordinate
        -  iy: image y coordinate
        -  isys: image system (i.e. 'image')
        -  px: physical x coordinate
        -  py: physical y coordinate
        -  psys: currently selected pixel-based system (i.e. 'image' or
           'physical') for the above px, py values
        -  ra: ra in degrees (if WCS is available)
        -  dec: dec in degrees (if WCS is available)
        -  wcssys: wcs system (if WCS is available)
        -  val: floating point pixel value
        -  val3: pixel value as a string truncated to 3 decimal digits
        -  vstr: string containing value and position info
        -  id: id of the image
        -  file: filename of the image
        -  object: object name of the image from the FITS header
        """
        return self.send({'cmd': 'GetValPos', 'args': args})

    def PixToWCS(self, *args):
        """
        Convert image pixel position to WCS position

        call:

        wcsobj  = PixToWCS(x, y)

        where:

        -  x: x image coordinate
        -  y: y image coordinate

        returns:

        -  wcsobj: world coordinate system object

        The wcs object contains the following properties:

        -  ra: right ascension in floating point degrees
        -  dec: declination in floating point degrees
        -  sys: current world coordinate system being used
        -  str: string of wcs in current system ('[ra] [dec] [sys]')

        """
        return self.send({'cmd': 'PixToWCS', 'args': args})

    def WCSToPix(self, *args):
        """
        Convert WCS position to image pixel position

        call:

        pixobj  = WCSToPix(ra, dec)

        where:

        -  ra: right ascension in floating point degrees
        -  dec: declination in floating point degrees

        returns:

        -  pixobj: pixel object

        The pixel object contains the following properties:

        -  x: x image coordinate
        -  y: y image coordinate
        -  str: string of pixel values ('[x]' '[y]')
        """
        return self.send({'cmd': 'WCSToPix', 'args': args})

    def ImageToDisplayPos(self, *args):
        """
        Get the display coordinates from the image coordinates

        call:

        dpos  = ImageToDisplayPos(ipos)

        where:

        -  ipos: image position object containing x and y image coordinate
           values

        returns:

        -  dpos: display position object containing x and y display
           coordinate values

        Get display (screen) coordinates from image coordinates. Note that
        image coordinates are one-indexed, as per FITS conventions, while
        display coordinate are 0-indexed.
        """
        return self.send({'cmd': 'ImageToDisplayPos', 'args': args})

    def DisplayToImagePos(self, *args):
        """
        Get the image coordinates from the display coordinates

        call:

        ipos  = DisplayToImagePos(dpos)

        where:

        -  dpos: display position object containing x and y display
           coordinate values

        returns:

        -  ipos: image position object containing x and y image coordinate
           values

        Note that image coordinates are one-indexed, as per FITS conventions,
        while display coordinate are 0-indexed.
        """
        return self.send({'cmd': 'DisplayToImagePos', 'args': args})

    def ImageToLogicalPos(self, *args):
        """
        Get the logical coordinates from the image coordinates

        call:

        lpos  = ImageToLogicalPos(ipos, lcs)

        where:

        -  ipos: image position object containing x and y image coordinate
           values

        returns:

        -  lpos: logical position object containing x and y logical
           coordinate values

        Logical coordinate systems include: 'physical' (defined by LTM/LTV
        keywords in a FITS header), 'detector' (DTM/DTV keywords), and
        'amplifier' (ATM/ATV keywords). Physical coordinates are the most
        common. In the world of X-ray astronomy, they refer to the 'zoom 1'
        coordinates of the data file.

        This routine will convert from image to logical coordinates. By
        default, the current logical coordinate system is used. You can specify
        a different logical coordinate system (assuming the appropriate
        keywords have been defined).
        """
        return self.send({'cmd': 'ImageToLogicalPos', 'args': args})

    def LogicalToImagePos(self, *args):
        """
        Get the image coordinates from the logical coordinates

        call:

        ipos  = LogicalToImagePos(lpos, lcs)

        where:

        -  lpos: logical position object containing x and y logical
           coordinate values

        returns:

        -  ipos: image position object containing x and y image coordinate
           values

        Logical coordinate systems include: 'physical' (defined by LTM/LTV
        keywords in a FITS header), 'detector' (DTM/DTV keywords), and
        'amplifier' (ATM/ATV keywords). Physical coordinates are the most
        common. In the world of X-ray astronomy, they refer to the 'zoom 1'
        coordinates of the data file.

        This routine will convert from logical to image coordinates. By
        default, the current logical coordinate system is used. You can specify
        a different logical coordinate system (assuming the appropriate
        keywords have been defined).
        """
        return self.send({'cmd': 'LogicalToImagePos', 'args': args})

    def GetWCSUnits(self, *args):
        """
        Get the current WCS units

        call:

        unitsstr  = GetWCSUnits()

        returns:

        -  unitstr: 'pixels', 'degrees' or 'sexagesimal'
        """
        return self.send({'cmd': 'GetWCSUnits', 'args': args})

    def SetWCSUnits(self, *args):
        """
        Set the current WCS units

        call:

        SetWCSUnits(unitsstr)

        where:

        -  unitstr: 'pixels', 'degrees' or 'sexagesimal'

        Set the current WCS units.
        """
        return self.send({'cmd': 'SetWCSUnits', 'args': args})

    def GetWCSSys(self, *args):
        """
        Get the current World Coordinate System

        call:

        sysstr  = GetWCSSys()

        returns:

        -  sysstr: current World Coordinate System ('FK4', 'FK5', 'ICRS',
           'galactic', 'ecliptic', 'image', or 'physical')
        """
        return self.send({'cmd': 'GetWCSSys', 'args': args})

    def SetWCSSys(self, *args):
        """
        Set the current World Coordinate System

        call:

        SetWCSSys(sysstr)

        where:

        -  sysstr: World Coordinate System ('FK4', 'FK5', 'ICRS',
           'galactic', 'ecliptic', 'image', or 'physical')

        Set current WCS system. The WCS systems are available only if WCS
        information is contained in the FITS header. Also note that 'physical'
        coordinates are the coordinates tied to the original file. They are
        mainly used in X-ray astronomy where individually detected photon
        events are binned into an image, possibly using a blocking factor. For
        optical images, image and physical coordinate usually are identical.
        """
        return self.send({'cmd': 'SetWCSSys', 'args': args})

    def GaussBlurData(self, *args):
        """
        Gaussian blur of raw data

        call:

        JS9.GaussBlurData(sigma, opts)

        where:

        - sigma: sigma of Gaussian function
        - opts: options object

        This routine creates a new raw data layer called "gaussBlur"
        in which the image pixel values are blurred using a Gaussian
        function with the specified sigma. The routine uses the fast
        Gaussian blur algorithm (approximating a full Gaussian blur
        with three passes of a box blur) described in:
        http://blog.ivank.net/fastest-gaussian-blur.html.
        """
        return self.send({'cmd': 'GaussBlurData', 'args': args})

    def ImarithData(self, *args):
        """
        Perform image arithmetic on raw data

        call:

        JS9.ImarithData(op, arg1, opts)

        where:

        - op: image operation: "add", "sub", "mul", "div", "min", "max", "reset"
        - arg1: image handle, image id or numeric value
        - opts: options object

        The JS9.ImarithData() routine performs basic arithmetic
        (addition, subtraction, multiplication, division, minimum,
        maximum, average) between the currently displayed image and
        either another image or a constant value. The first op
        argument is a string, as detailed above. The second arg1
        argument can be a numeric value or an image id. In the former
        case, the constant value is applied to each pixel in the
        image. In the latter case, the operation is performed between
        the corresponding pixels in the two images. For example:

          >>> JS9.ImarithData("max", "foo.fits")

        will make a new data layer of the currently displayed image, where
        each pixel is the larger value from that image and the foo.fits image
        (which can be in any display).

        This routine creates a new raw data layer called "imarith"
        containing the results of the operation. Successive calls to
        this routine are cumulative, so that you can build up a more
        complex operation from simple ones. For example:

          >>> # foo.fits is displayed in the "myJS9" display
          >>> myim = JS9.GetImage()
          >>> JS9.ImarithData("max", myim)
          >>> JS9.ImarithData("add", 2.718)

        will make a new data layer where each pixel is the larger value from
        the two images, after which an approximation of the irrational number
        e is added to each pixel.

        The special reset operation deletes the "imarith" raw data
        layer, allowing you to start afresh.

        The bitpix value of the new "imarith" layer is chosen as follows:

        - for operations between two images, bitpix the "larger" of
        the two images (where float is "larger" than int).

        -  for operations between an image and a constant, bitpix of -32
        (single float) is chosen unless the image itself has bitpix of -64, in
        which case the double float bitpix is chosen.

        You can override the choice of bitpix by passing a bitpix property
        in the optional opts object.

        Finally, note that the two images must have the same dimensions. We
        might be able to remove this restriction in the future, although
        it is unclear how one lines up images of different dimensions.
        """
        return self.send({'cmd': 'ImarithData', 'args': args})

    def ShiftData(self, *args):
        """
        Shift raw data

        call:

        JS9.ShiftData(x, y, opts)

        where:

        - x: number of pixels to shift in the x (width) direction
        - y: number of pixels to shift in the y (height) direction
        - opts: options object

        This routine creates a new raw data layer called "shift" in which
        the pixels are shifted from the original image array by the specified
        amount in x and/or y. The results of successive shifts are
        cumulative. The routine is used by the Harvard-Smithsonian Center for
        Astrophysics MicroObservatory project interactively to align images
        that are only slightly offset from one another.
        """
        return self.send({'cmd': 'ImarithData', 'args': args})

    def FilterRGBImage(self, *args):
        """
        Apply a filter to the RGB image

        call:

        JS9.FilterRGBImage(filter, args)

        where:

        - filter: name of image filter to apply to the RGB data
        - args: filter-specific arguments, where applicable

        In JS9, you can change the raw data (and hence the displayed
        image) using routines such as JS9.GaussBlurData() or the more
        general JS9.RawDataLayer(). You also can apply image
        processing techniques directly to the displayed RGB image
        without changing the underlying raw data, using this
        routine. The web has an overwhelming amount of information
        about image processing.  A good technical article concerning
        the use of image filters with Javascript and the HTML5 canvas
        is available at:
        http://www.html5rocks.com/en/tutorials/canvas/imagefilters/

        The JS9.FilterRGBImage() routine supports a number of image
        processing routines, which are listed below.  To call one of
        them using JS9.FilterRGBImage(), supply the filter name,
        followed by any filter-specific arguments, e.g.:

          >>> JS9.FilterRGBImage("luminance")
          >>> JS9.FilterRGBImage("duotone", "g")
          >>> JS9.FilterRGBImage("convolve", [-1,-1,-1,-1,8,-1,-1,-1,-1])

        You can, of course, use the default arguments where applicable.

        Note that the standard JS9 colormaps, scale, contrast and bias
        selections are applied to the raw data to regenerate the RGB
        image. Thus, if you use any of the image processing techniques
        listed below and then change colormap, contrast, bias, or
        scale, you will undo the applied image processing. This is a
        good way to reset the displayed image. The same thing can be
        accomplished programmatically by specifying "reset" as the
        filter name:

          >>> JS9.FilterRGBImage("reset")

        The following simple image processing filters are available:
        - luminance():convert to greyscale using the CIE luminance:
        0.2126*r + 0.7152*g + 0.0722*b

        - greyscale():convert to greyscale using the standard greyscale:
        0.3*r + 0.59*g + 0.11*b

        - greyscaleAvg():convert to greyscale using averaging:
        (r+g+b) / 3

        - brighten(val): add const value to each pixel to change the brightness:
        [r + val, g + val, b + val]

        - noise(v1, v2): add random noise:
        pixel += Math.floor((Math.random()*(v2-v1)) - v2),
        defaults are v1=-30, v2=30

        - duotone("r"|"g"|"b"): remove a color by setting it to
        the avg of the two others: r=(g+b)/2, default color is "r"

        - invert(): the RGB channels of the image are inverted:
        [255-r, 255-g, 255-b, a]

        - pixelate(size):make image look coarser by creating a square tiling
        effect of the specified size, default size is 2

        - sepia(): image takes on shades of brown, like an antique photograph

        - contrast(val): change the difference in brightness between the min
        and max intensity of a pixel, default val is 2

        - threshold(thresh, low, high):create a two-color image in which pixels
        less bright than thresh are assigned the low value (default 0 for
        black), otherwise the high value (default: 255 for white)

        - gamma(gcorr): apply the nonlinear gamma operation, used to code and
        decode luminance values in video or still image systems:
        out = pow(in, gcorr), default gcorr is 0.2

        - posterize(): convert a smooth gradation of tone to regions
        of fewer tones, with abrupt changes between them

        - scatter(): scatters the colors of a pixel in its neighborhood, akin
        to viewing through brittle cracked glass

        - solarize(): which image is wholly or partially reversed in
        tone. Dark areas appear light or light areas appear dark.

        The following image convolutions are available:

        - convolve(weights, [opaque]) convolve the image using the
        weights array as a square convolution matrix. If opaque is true
        (default), the image will have an opaque alpha channel, otherwise the
        alpha is convolved as well.

        - sobel(): use the Sobel operator to create an image that
              emphasizes the edges

        - medianFilter(): noise reduction technique that replaces each
              pixel with the median of neighboring pixels

        - gaussBlur5(): image pixel values are blurred using a 5x5 Gaussian

        - edgeDetect(): detect edges using the kernel
        [ -1, -1, -1, -1, 8, -1, -1, -1, -1 ]

        - sharpen(val): sharpen the image using the kernel
        [ 0, -3, 0, -3, val, -3, 0, -3, 0 ]

        - blur(): blur the image using the kernel
        [ 1, 2, 1, 2, 1, 2, 1, 2, 1 ]

        - emboss(val): produce embossing effect using the kernel
        [-18, -9, 9, -9, 100 - val, 9, 0, 9, 18 ]

        - lighten(val): apply the kernel
        [ 0, 0, 0, 0, val, 0, 0, 0, 0 ],
        default val of 12/9 lightens the image

        - darken(val): apply the kernel
        [ 0, 0, 0, 0, val, 0, 0, 0, 0],
        default val of 6/9 darkens the image

        With no arguments, the routine returns an array of available filters:

          >>> JS9.FilterRGBImage()
          ["convolve", "luminance", ..., "blur", "emboss", "lighten", "darken"]
        """
        return self.send({'cmd': 'FilterRGBImage', 'args': args})

    def ReprojectData(self, *args):
        """
        Reproject an image using a specified WCS

        call:

        JS9.ReprojectData(wcsim, opts)

        where:

        - wcsim: image containing the WCS used to perform the reprojection
        - opts: options object

        JS9.ReprojectData() creates a new raw data layer (with default id of
        "reproject") in which the pixels are reprojected using the WCS from
        another image. The mProjectPP program from the Montage software suite
        is used to perform the reprojection.  Please read the documentation on
        mProjectPP from the Montage web site, which includes this explanation:

        mProjectPP performs a plane-to-plane transform on the input
        image, and is an adaptation of the Mopex algorithm and
        developed in collaboration with the Spitzer Space
        Telescope. It provides a speed increase of approximately a
        factor of 30 over the general-purpose mProject. However,
        mProjectPP is only suitable for projections which can be
        approximated by tangent-plane projections (TAN, SIN, ZEA, STG,
        ARC), and is therefore not suited for images covering large
        portions of the sky. Also note that it does not directly
        support changes in coordinate system (i.e. equatorial to
        galactic coordinates), though these changes can be facilitated
        by the use of an alternate header.

        The wcsim argument is an image id, image filename, or image
        object pointing to the WCS image.

        The opts object can contain the following reproject-specific properties:

        - rawid: the id of the raw data layer to create (default: "reproject")
        - cmdswitches: a string containing mProjectPP command line switches

        The cmdswitches will be prepended to the mProjectPP command line:

        {cmdswitches: "-d 1 -z .75"}

        will set the mProjectPP debugging and the drizzle factor,
        resulting in a command line that looks like this:

        mProjectPP -d 1 -z .75 -s statusfile in.fits out.fits template.hdr

        See the mProjectPP documentation for more information about
        command switches.

        Reprojection is an intensive process which can take a
        considerable amount of memory and processing time. To avoid
        crashes, we currently restrict the WCS image size used for
        reprojection to a value defined by JS9.REPROJDIM, currently
        2200 x 2200. Even this might be too large for iOS devices
        under certain circumstances, although issues regarding memory
        are evolving rapidly.
        """
        return self.send({'cmd': 'ReprojectData', 'args': args})

    def SaveSession(self, *args):
        """
        Save an image session to a file

        call:

        JS9.SaveSession(session)

        where:

        - session: name of the file to create when saving this session

        This routine saves all essential session information about the
        currently displayed image (filename, scaling, colormap, contrast/bias,
            zoom, regions, catalogs, etc) in a json-formatted file. You can
            subsequently load this file into JS9 to restore the image session.

        The session file is a text file and can be edited, subject to the
            usual rules of json formatting. For example, you can change the
            colormap, scaling, etc. after the fact.

            Don't forget that the file is saved by the browser, in whatever
            location you have set up for downloads.

        The session file contains a file property near the top that
        specifies the location of the image. A local file usually will
        contain an absolute path or a path relative to the web page
        being displayed.  However, if the image was originally opened
        using drag-and-drop, no pathname information is available, in
        accordance with standard web security protocols. In this case,
        you must edit the session file to supply the path (either
        absolute or relative to the web page) before re-loading the
        session.
        """
        return self.send({'cmd': 'SaveSession', 'args': args})

    def LoadSession(self, *args):
        """
        Load a previously saved image session from a file

        call:

        JS9.LoadSession(session)

        where:

        - session: name of the session file to load

        Restore an image session by loading a json-formatted session file. The
        image itself is retrieved and loaded, and all of the saved parameters
        and graphics (scale, colormap, regions, catalogs etc) are applied to
        the display.

        The session file contains a file property near the top that
        specifies the location of the image. A local file usually will
        contain an absolute path or a path relative to the web page
        being displayed.  However, if the image was originally opened
        using drag-and-drop, no pathname information is available, in
        accordance with standard web security protocols. In this case,
        you must edit the session file to supply the path (either
        absolute or relative to the web page) before re-loading the
        session.

        Note that the raw data file itself is not saved (only its
        pathname), so you must have access to that file in order to
        restore a session. However, the data file need not be in the
        same location as it was originally: you can adjust the path of
        the data file by editing the file property as needed.
        """
        return self.send({'cmd': 'LoadSession', 'args': args})

    def NewShapeLayer(self, *args):
        """
        Create a new shape layer

        call:

        lid  = NewShapeLayer(layer, opts)

        where:

        -  layer: name of the layer to create
        -  opts: default options for this layer

        returns:

        -  lid: layer id

        This routine creates a new named shape layer. You can then, add,
        change, and remove shapes in this layer using the routines below. The
        catalogs displayed by the Catalog plugin are examples of separate shape
        layers.  The optional opts parameter allows you to specify default
        options for this layer. You can set a default for any property needed
        by your shape layer. See JS9.Regions.opts in js9.js for an example of
        the default options for the regions layer.
        """
        return self.send({'cmd': 'NewShapeLayer', 'args': args})

    def ShowShapeLayer(self, *args):
        """
        Show or hide the specified shape layer

        call:

        ShowShapeLayer(layer, mode)

        where:

        -  layer: name of layer
        -  mode: true (show layer) or false (hide layer)

        Shape layers can be hidden from display. This could be useful, for
        example, if you have several catalogs loaded into a display and want to
        view one at a time.
        """
        return self.send({'cmd': 'ShowShapeLayer', 'args': args})

    def ActiveShapeLayer(self, *args):
        """
        Make the specified shape layer the active layer

        call:

        ActiveShapeLayer(layer)

        where:

        - layer: name of layer

        returns:

        -  active: the active shape layer (if no args are specified)

        For a given image, one shape layer at a time is active, responding to
        mouse and touch events. Ordinarily, a shape layer becomes the active
        layer when it is first created and shapes are added to it. Thus, the
        first time you create a region, the regions layer becomes active. If
        you then load a catalog into a catalog layer, that layer becomes active.

        If no arguments are supplied, the routine returns the currently active
        layer. Specify the name of a layer as the first argument to make it
        active. Note that the specified layer must be visible.
        """
        return self.send({'cmd': 'ActiveShapeLayer', 'args': args})

    def AddShapes(self, *args):
        """
        Add one or more shapes to the specified layer

        call:

        AddShapes(layer, sarr, opts)

        where:

        -  layer: name of layer
        -  sarr: a shape string, shape object, or an array of shape objects
        -  opts: global values to apply to each created shape

        returns:

        -  id: id of last shape created

        The sarr argument can be a shape ('annulus', 'box', 'circle',
        'ellipse', 'point', 'polygon', 'text'), a single shape object, or an
        array of shape objects. Shape objects contain one or more properties,
        of which the most important are:

        -  shape: 'annulus', 'box', 'circle', 'ellipse', 'point', 'polygon',
           'text' [REQUIRED]
        -  x: image x position
        -  y: image y position
        -  dx: increment from current image x position
        -  dy: increment from current image y position
        -  tags: comma separated list of tag strings
        -  radii: array of radii for annulus shape
        -  width: width for box shape
        -  height: height for box shape
        -  radius: radius value for circle shape
        -  r1: x radius for ellipse shape (misnomer noted)
        -  r2: y radius for ellipse shape (misnomer noted)
        -  pts: array of objects containing x and y positions, for polygons
        -  points: array of objects containing x and y offsets from the
           specified center, for polygons
        -  angle: angle in degrees for box and ellipse shapes
        -  color: shape color (string name or #rrggbb syntax)
        -  text: text associated with text shape

        Other available properties include:

        -  fixinplace: if true, shape cannot be moved or resized
        -  lockMovementX: shape cannot be moved in the x direction
        -  lockMovementY: shape cannot be moved in the y direction
        -  lockRotation: shape cannot be rotated
        -  lockScalingX: shape cannot be resized in the x direction
        -  lockScalingY: shape cannot be resized in the y direction
        -  fontFamily: font parameter for text shape
        -  fontSize: font parameter for text shape
        -  fontStyle: font parameter for text shape
        -  fontWeight: font parameter for text shape
        """
        return self.send({'cmd': 'AddShapes', 'args': args})

    def RemoveShapes(self, *args):
        """
        Remove one or more shapes from the specified shape layer

        call:

        RemoveShapes(layer, shapes)

        where:

        -  layer: name of layer
        -  shapes: which shapes to remove
        """
        return self.send({'cmd': 'RemoveShapes', 'args': args})

    def GetShapes(self, *args):
        """
        Get information about one or more shapes in the specified shape
        layer

        call:

        GetShapes(layer, shapes)

        where:

        -  layer: name of layer
        -  shapes: which shapes to retrieve

        returns:

        -  sarr: array of shape objects

        Each returned shape object contains the following properties:

        -  id: numeric region id (assigned by JS9 automatically)
        -  mode: 'add', 'remove', or 'change'
        -  shape: region shape ('annulus', 'box', 'circle', 'ellipse',
           'point', 'polygon', 'text')
        -  tags: comma delimited list of region tags (e.g., 'source',
           'include')
        -  color: region color
        -  x,y: image coordinates of region
        -  size: object containing width and height for box region
        -  radius: radius value for circle region
        -  radii: array of radii for annulus region
        -  eradius: object containing x and y radii for ellipse regions
        -  pts: array of objects containing x and y positions, for polygons
        -  angle: angle in degrees for box and ellipse regions
        """
        return self.send({'cmd': 'GetShapes', 'args': args})

    def ChangeShapes(self, *args):
        """
        Change one or more shapes in the specified layer

        call:

        ChangeShapes(layer, shapes, opts)

        where:

        -  layer: name of layer
        -  shapes: which shapes to change
        -  opts: object containing options to change in each shape

        Change one or more shapes. The opts object can contain the parameters
        described in the JS9.AddShapes() section. However, you cannot (yet)
        change the shape itself (e.g. from 'box' to 'circle').
        """
        return self.send({'cmd': 'ChangeShapes', 'args': args})

    def AddRegions(self, *args):
        """
        Add one or more regions to the regions layer

        call:

        id  = AddRegions(rarr, opts)

        where:

        -  rarr: a shape string, region object or an array of region objects
        -  opts: global values to apply to each created region

        returns:

        -  id: id of last region created

        The rarr argument can be a region shape ('annulus', 'box', 'circle',
        'ellipse', 'point', 'polygon', 'text'), a single region object, or an
        array of region objects. Region objects contain one or more properties,
        of which the most important are:

        -  shape: 'annulus', 'box', 'circle', 'ellipse', 'point', 'polygon',
           'text' [REQUIRED]
        -  x: image x position
        -  y: image y position
        -  lcs: object containing logical x, y and sys (e.g. 'physical')
        -  dx: increment from current image x position
        -  dy: increment from current image y position
        -  tags: comma separated list of tag strings
        -  radii: array of radii for annulus region
        -  width: width for box region
        -  height: height for box region
        -  radius: radius value for circle region
        -  r1: x radius for ellipse region (misnomer noted)
        -  r2: y radius for ellipse region (misnomer noted)
        -  pts: array of objects containing x and y positions for polygons
        -  points: array of objects containing x and y offsets from the
           center for polygons
        -  angle: angle in degrees for box and ellipse regions
        -  color: region color (string name or #rrggbb syntax)
        -  text: text associated with text region

        Other available properties include:

        -  fixinplace: if true, region cannot be moved or resized
        -  lockMovementX: region cannot be moved in the x direction
        -  lockMovementY: region cannot be moved in the y direction
        -  lockRotation: region cannot be rotated
        -  lockScalingX: region cannot be resized in the x direction
        -  lockScalingY: region cannot be resized in the y direction
        -  fontFamily: font parameter for text region
        -  fontSize: font parameter for text region
        -  fontStyle: font parameter for text region
        -  fontWeight: font parameter for text region
        """
        return self.send({'cmd': 'AddRegions', 'args': args})

    def GetRegions(self, *args):
        """
        Get information about one or more regions

        call:

        rarr  = GetRegions(regions)

        where:

        -  regions: which regions to retrieve

        returns:

        -  rarr: array of region objects

        Each returned region object contains the following properties:

        -  id: numeric region id (assigned by JS9 automatically)
        -  mode: 'add', 'remove' or 'change'
        -  shape: region shape ('annulus', 'box', 'circle', 'ellipse',
           'point', 'polygon', 'text')
        -  tags: comma delimited list of region tags (e.g., 'source',
           'include')
        -  color: region color
        -  x,y: image coordinates of region
        -  radii: array of radii for annulus region
        -  width: width for box region
        -  height: height for box region
        -  radius: radius value for circle region
        -  r1: x radius for ellipse region (misnomer noted)
        -  r2: y radius for ellipse region (misnomer noted)
        -  pts: array of objects containing x and y positions, for polygons
        -  points: array of objects containing x and y offsets from the
           specified center, for polygons
        -  angle: angle in degrees for box and ellipse regions
        -  wcsstr: region string in wcs coordinates
        -  wcssys: wcs system (e.g. 'FK5')
        -  imstr: region string in image or physical coordinates
        -  imsys: image system ('image' or 'physical')
        """
        return self.send({'cmd': 'GetRegions', 'args': args})

    def ChangeRegions(self, *args):
        """
        Change one or more regions

        call:

        ChangeRegions(regions, opts)

        where:

        -  regions: which regions to change
        -  opts: object containing options to change in each region

        Change one or more regions. The opts object can contain the parameters
        described in the JS9.AddRegions() section. However, you cannot (yet)
        change the shape itself (e.g. from 'box' to 'circle'). See
        js9onchange.html for examples of how to use this routine.
        """
        return self.send({'cmd': 'ChangeRegions', 'args': args})

    def RemoveRegions(self, *args):
        """
        Remove one or more regions from the region layer

        call:

        RemoveRegions(regions)

        where:

        -  regions: which regions to remove
        """
        return self.send({'cmd': 'RemoveRegions', 'args': args})

    def SaveRegions(self, *args):
        """
        Save regions from the current image to a file

        call:

        JS9.SaveRegions(filename, which, layer)

        where:

        - filename: output file name
        - which: which regions to save (default is "all")
            - layer: which layer save (default is "regions")

        Save the current regions for the displayed image as JS9 regions file. If
        filename is not specified, the file will be saved as "js9.reg".

            Don't forget that the file is saved by the browser, in whatever
            location you have set up for downloads.

            If the which argument is not specified, it defaults to "all". You
            can specify "selected" to return information about the selected
            regions, or a tag value to save regions having that tag.

            If the layer argument is not specified, it defaults to "regions",
            i.e.  the usual regions layer. You can specify a different layer,
            e.g., if you want to save a catalog layer as a region file
            (since SaveCatalog() will save the data in table format instead
            of as regions).
        """
        return self.send({'cmd': 'SaveRegions', 'args': args})

    def LoadRegions(self, *args):
        """
        Load regions from a file into the current image

        call:

        JS9.LoadRegions(filename)

        where:

        - filename: input file name or URL

        Load the specified regions file into the displayed image. The filename,
        which must be specified, can be a local file (with absolute path or a
        path relative to the displayed web page) or a URL.
        """
        return self.send({'cmd': 'LoadRegions', 'args': args})

    def LoadCatalog(self, *args):
        """
            Load an astronomical catalog

        call:

        JS9.LoadCatalog(layer, table, opts)

        where:

            - name of shape layer into which to load the catalog
            - table: string or blob containing the catalog table
            - opts: catalog options

            Astronomical catalogs are a special type of JS9 shape layer, in which
            the shapes have been generated from a tab-delimited text file of
            columns, including two columns that contain RA and Dec values. An
            astronomical catalog can have a pre-amble of comments, which, by
            default, have a '#' character in the first column.

            The JS9.LoadCatalog() routine will read a file in this format,
            processing the data rows by converting the RA and Dec values into
            image position values that will be displayed as shapes in a new
            catalog layer.

            The first argument to the JS9.LoadCatalog() routine is the name
            of the shape layer that will contain the objects in the catalog.
            Specifying the name of an existing layer is valid: previous shapes
            in that layer will be removed.

            The second argument should be a string containing the table
            data described above (the result of reading a file, performing
            a URL get, etc.)

            The third argument is an optional object used to specify parameters,
            including:

            - xcol: name of the RA column in the table
            - ycol: name of the Dec column in the table
            - wcssys: wcs system (FK4, FK5, ICRS, galactic, ecliptic)
            - shape: shape of catalog object
            - color: color of catalog shapes
            - width: width of box catalog shapes
            - height: height of box catalog shapes
            - radius: radius of circle catalog shapes
            - r1: r1 of ellipse catalog shapes
            - r2: r2 of ellipse catalog shapes
            - tooltip: format of tooltip string to display for each object
            - skip: comment character in table file

            Most of these properties have default values that are stored
            in the JS9.globalOpts.catalogs object. The values listed above
            also can be changed by users via the Catalog tab in the
            Preferences plugin.

            """
        return self.send({'cmd': 'LoadCatalog', 'args': args})

    def SaveCatalog(self, *args):
        """
            Save an astronomical catalog to a file

        call:

        JS9.SaveCatalog(filename, which)

        where:

            - filename: output file name
            - which: layer containing catalog objects to save

            Save the specified catalog layer as a text file. If filename is not
            specified, the file will be saved as [layer].cat.

            Don't forget that the file is saved by the browser, in whatever
            location you have set up for downloads.

            If the which argument is not specified, the catalog associated
            with the current active layer will be saved. In either case, the
            layer to save must actually be a catalog created from a tab-delimited
            file (or URL) of catalog objects (not, for example, the regions layer).
        """
        return self.send({'cmd': 'SaveCatalog', 'args': args})

    def RunAnalysis(self, *args):
        """
        Run a simple server-side analysis task

        call:

        RunAnalysis(name, parr)

        where:

        -  name: name of analysis tool
        -  parr: optional array of macro-expansion options for command line

        The JS9.RunAnalysis() routine is used to execute a server-side analysis
        task and return the results for further processing within Python.

        NB: Prior to JS9 v1.10, this routine displayed the results on the JS9
        web page instead of returning them to Python.

        The optional parr array of parameters is passed to the JS9 analysis
        macro expander so that values can be added to the command line. The
        array is in jQuery name/value serialized object format, which is
        described here:

                http://api.jquery.com/serializeArray/
        """
        return self.send({'cmd': 'RunAnalysis', 'args': args})

    def SavePNG(self, *args):
        """
        Save image as a PNG file

        call:

        JS9.SavePNG(filename)

        where:

        - filename: output file name

        Save the currently displayed image as a PNG file. If filename is not
        specified, the file will be saved as "js9.png". The image is saved
        along with the graphical overlays (regions, etc.).

            Don't forget that the file is saved by the browser, in whatever
            location you have set up for downloads.
        """
        return self.send({'cmd': 'SavePNG', 'args': args})

    def SaveJPEG(self, *args):
        """
        Save image as a JPEG file

        call:

        JS9.SaveJPEG(filename, quality)

        where:

        - filename: output file name
        - quality: a number between 0 and 1 indicating image quality

        Save the currently displayed image as a JPEG file. If filename is not
        specified, the file will be saved as "js9.jpeg". The image is saved
        along with the graphical overlays (regions, etc.). If quality
        parameter is not specified, a suitable default is used. On FireFox (at
        least), this default values is 0.95 (I think).

            Don't forget that the file is saved by the browser, in whatever
            location you have set up for downloads.
        """
        return self.send({'cmd': 'SaveJPEG', 'args': args})

    def GetFITSHeader(self, *args):
        """
        Get FITS header as a string

        call:

        JS9.GetFITSHeader(nlflag)

        where:

        - nlflag: true if newlines should added to each card

        Return the FITS header as a string. By default, the returned string
        contains the 80-character FITS cards all concatenated together. If
        nlflag is true, each card will have a new-line appended.

        Note that the JS9.GetImageData() routine also returns the FITS
        header, but as an object whose properties contain the header
        values. For example, obj.SIMPLE will usually have a value of
        true, obj.BITPIX will have contain the bits/pixel, etc. This
        object is more useful for programming tasks, but does not
        contain the FITS comments associated with each header card.
        """
        return self.send({'cmd': 'GetFITSHeader', 'args': args})

    def Print(self, *args):
        """
        Print the current image
        """
        return self.send({'cmd': 'Print', 'args': args})

    def ResizeDisplay(self, *args):
        """
        Change the width and height of the JS9 display

        call:

        ResizeDisplay(width, height)

        where:

        - width: new width of the display in HTML pixels
        - height: new height of the display in HTML pixels
        - opts: optional object containing resize parameters

        You can resize the JS9 display element by supplying new width and
        height parameters. The div on the web page will be resized and the
        image will be re-centered in the new display. If the display size has
        been increased, more of the image will be displayed as needed (up to
        the new size of the display). For example, if the original display was
        512x512 and you increase it to 1024x1024, a 1024x1024 image will now
        be displayed in its entirety.

        The opts object can contain the following properties:

        - resizeMenubar: change the width of the menubar as well

        The default for resizeMenubar is True, so you only need
        to pass this property if you do not want to perform the resize.
        """
        return self.send({'cmd': 'ResizeDisplay', 'args': args})

    def DisplayHelp(self, *args):
        """
        Display help in a light window

        call:

        DisplayHelp(name)

        where:

        -  name: name of a help file or url of a web site to display

        The help file names are the property names in JS9.helpOpts (e.g.,
        'user' for the user page, 'install' for the install page, etc.).
        Alternatively, you can specify an arbitrary URL to display (just
        because).
        """
        return self.send({'cmd': 'DisplayHelp', 'args': args})

    def analysis(self, *args):
        """
        run/list analysis for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string.
        """
        return self.send({'cmd': 'analysis', 'args': args})

    def colormap(self, *args):
        """
        set/get colormap for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string: 'colormap contrast bias'
        """
        return self.send({'cmd': 'colormap', 'args': args})

    def cmap(self, *args):
        """
        set/get colormap for current image (alias)

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string: 'colormap contrast bias'
        """
        return self.send({'cmd': 'cmap', 'args': args})

    def colormaps(self, *args):
        """
        get list of available colormaps

        No setter routine is provided.
        Returned results are of type string: 'grey, red, ...'
        """
        return self.send({'cmd': 'colormaps', 'args': args})

    def image(self, *args):
        """
        get name of currently loaded image or display specified image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string.
        """
        return self.send({'cmd': 'image', 'args': args})

    def images(self, *args):
        """
        get list of currently loaded images

        No setter routine is provided.
        Returned results are of type string.
        """
        return self.send({'cmd': 'images', 'args': args})

    def load(self, *args):
        """
        load image(s)

        No getter routine is provided.
        """
        return self.send({'cmd': 'load', 'args': args})

    def pan(self, *args):
        """
        set/get pan location for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string: 'x y'
        """
        return self.send({'cmd': 'pan', 'args': args})

    def regions(self, *args):
        """
        add region to current image or list all regions

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string.
        """
        return self.send({'cmd': 'regions', 'args': args})

    def region(self, *args):
        """
        add region to current image or list all regions (alias)

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string.
        """
        return self.send({'cmd': 'region', 'args': args})

    def resize(self, *args):
        """
        set/get size of the JS9 display

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string: 'width height'
        """
        return self.send({'cmd': 'resize', 'args': args})

    def scale(self, *args):
        """
        set/get scaling for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string: 'scale scalemin scalemax'
        """
        return self.send({'cmd': 'scale', 'args': args})

    def scales(self, *args):
        """
        get list of available scales

        No setter routine is provided.
        Returned results are of type string: 'linear, log, ...'
        """
        return self.send({'cmd': 'scales', 'args': args})

    def wcssys(self, *args):
        """
        set/get wcs system for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string.
        """
        return self.send({'cmd': 'wcssys', 'args': args})

    def wcsu(self, *args):
        """
        set/get wcs units used for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are of type string.
        """
        return self.send({'cmd': 'wcsu', 'args': args})

    def wcssystems(self, *args):
        """
        get list of available wcs systems

        No setter routine is provided.
        Returned results are of type string: 'FK4, FK5, ...'
        """
        return self.send({'cmd': 'wcssystems', 'args': args})

    def wcsunits(self, *args):
        """
        get list of available wcs units

        No setter routine is provided.
        Returned results are of type string: 'degrees, ...'
        """
        return self.send({'cmd': 'wcsunits', 'args': args})

    def zoom(self, *args):
        """
        set/get zoom for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values.

        Returned results are type integer or float.
        """
        return self.send({'cmd': 'zoom', 'args': args})
