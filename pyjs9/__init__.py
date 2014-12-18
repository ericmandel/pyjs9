import os
import StringIO
import json
import urllib
import base64

__all__ = ['JS9']

"""
pyjs9.py connects python and js9 via the js9Helper.js back-end server

- The JS9 class constructor connects to a single JS9 instance in a Web page.
- The JS9 object supports the JS9 Public API and a shorter command-line syntax.
- See: http://js9.si.edu/js9/help/publicapi.html
- Send/retrieve numpy arrays and astropy (or pyfits) hdulists to/from js9.

"""

# pyjs9 version
__version__ = '1.0'

# try to be a little bit neat with global parameters
js9Globals = {}

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
        if fits.__version__ >=  '2.2':
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

# utilities

def _decode_list(data):
    rv = []
    for item in data:
        if isinstance(item, unicode):
            item = item.encode('utf-8')
        elif isinstance(item, list):
            item = _decode_list(item)
        elif isinstance(item, dict):
            item = _decode_dict(item)
        rv.append(item)
    return rv

def _decode_dict(data):
    rv = {}
    for key, value in data.iteritems():
        if isinstance(key, unicode):
            key = key.encode('utf-8')
        if isinstance(value, unicode):
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
        if bitpix == 8:     return numpy.uint8
        elif bitpix == 16:  return numpy.int16
        elif bitpix == 32:  return numpy.int32
        elif bitpix == 64:  return numpy.int64
        elif bitpix == -32: return numpy.float32
        elif bitpix == -64: return numpy.float64
        elif bitpix == -16: return numpy.uint16
        else: raise ValueError, 'unsupported bitpix: %d' % bitpix

    def _np2bp(dtype):
        """
        Convert numpy datatype to FITS bitpix
        """
        if dtype == numpy.uint8:     return 8
        elif dtype == numpy.int16:   return 16
        elif dtype == numpy.int32:   return 32
        elif dtype == numpy.int64:   return 64
        elif dtype == numpy.float32: return -32
        elif dtype == numpy.float64: return -64
        elif dtype == numpy.uint16:  return -16
        else: raise ValueError, 'unsupported dtype: %s' % dtype

    def _bp2py(bitpix):
        """
        Convert FITS bitpix to python datatype
        """
        if bitpix == 8:     return 'B'
        elif bitpix == 16:  return 'h'
        elif bitpix == 32:  return 'l'
        elif bitpix == 64:  return 'q'
        elif bitpix == -32: return 'f'
        elif bitpix == -64: return 'd'
        elif bitpix == -16: return 'H'
        else: raise ValueError, 'unsupported bitpix: %d' % bitpix

    def _im2np(im):
        """
        Convert GetImageData object to numpy
        """
        w = int(im['width'])
        h = int(im['height'])
        d = 1
        bp = int(im['bitpix'])
        dtype = _bp2np(bp)
        dlen = h * w * abs(bp) / 8
        if js9Globals['retrieveAs'] == 'array':
            s = im['data'][0:h*w]
            if d > 1:
                arr = numpy.array(s, dtype=dtype).reshape((d,h,w))
            else:
                arr = numpy.array(s, dtype=dtype).reshape((h,w))
        elif js9Globals['retrieveAs'] == 'base64':
            s = base64.decodestring(im['data'])[0:dlen]
            if d > 1:
                arr = numpy.frombuffer(s, dtype=dtype).reshape((d,h,w))
            else:
                arr = numpy.frombuffer(s, dtype=dtype).reshape((h,w))
        else:
            raise ValueError, 'unknown retrieveAs type for GetImageData()'
        return arr

class JS9(object):
    """
    The JS9 class supports communication with an instance of JS9 in a Web
    page, utilizing the JS9 Public API calls as class methods.

    JS9's public access library is documented here:

    - http://js9.si.edu/js9/help/publicapi.html

    In addition, a number of special methods are implemented to facilitate data
    access to/from well-known Python objects:

    - GetNumpy: retrieve a FITS image or an array into a numpy array
    - SetNumpy: send a numpy array to js9 for display
    - GetFITS: retrieve a FITS image into an astropy (or pyfits) HDU list
    - SetFITS: send a astropy (or pyfits) HDU list to JS9 for display

    """

    def __init__(self, host='http://localhost:2718', id='JS9'):
        """
        :param host: host[:port] (default is 'http://localhost:2718')
        :param id: the JS9 display id (default is 'JS9')

        :rtype: JS9 object connected to a single instance of js9

        The JS9() contructor takes its first argument to be the host 
        (and optional port) on which the back-end js9Helper is running. 
        The default is 'http://localhost:2718', which generally will be
        the correct value for running locally. The default port (2718)
        will be added if no port value is found. The string 'http://' will
        be prefixed to the host if a URL protocol is not supplied. Thus, to
        connect to the main JS9 Web site, you can use host='js9.si.edu'.

        The   as its main argument. This
        is the default id for single instances of JS9 in a Web page.

        The second argument is a JS9 display id on the Web page. The
        default is 'JS9' ... which, not surprisingly, is the default
        JS9 display id.
        """
        self.__dict__['id']  = id
        # add default port, if necessary
        c = host.rfind(':')
        s = host.find('/')
        if( c <= s ):
            host += ":2718"
        if( s < 0 ):
            host = 'http://' + host
        self.__dict__['host']  = host
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
        self.send(None, msg="image")

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
        if obj == None:
            obj = {}
        obj['id'] = self.id
        jstr = json.dumps(obj)
        try:
            url = urllib.urlopen(self.host + '/' + msg, jstr)
        except IOError:
            raise IOError, "can't connect to %s. Is the JS9 helper running?" % self.host
        urtn = url.read()
        if urtn[0:6] == 'ERROR:':
            raise ValueError, urtn
        try:
            res = json.loads(urtn, object_hook=_decode_dict)
        except ValueError:       # not json
            res = urtn
        return res

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

            :rtype: 1 for success, 0 for failure

            After manipulating or otherwise modifying a fits hdulist (or
            making a new one), you can display it in js9 using the 'SetFITS'
            method, which takes the hdulist as its sole argument::

              >>> d.SetFITS(nhdul)
              1

            A return value of 1 indicates that js9 was contacted successfully,
            while a return value of 0 indicates a failure.
            """
            if not js9Globals['fits']:
                raise ValueError, 'SetFITS not defined (fits not found)'
            if type(hdul) != fits.HDUList:
                if js9Globals['fits'] == 1:
                    raise ValueError, 'requires astropy.HDUList as input'
                else:
                    raise ValueError, 'requires pyfits.HDUList as input'
            # in-memory string
            memstr = StringIO.StringIO()
            # write fits to memory string
            hdul.writeto(memstr, output_verify=js9Globals['output_verify'])
            # get memory string as an encoded string
            encstr = base64.b64encode(memstr.getvalue())
            # set up JS9 options
            opts = {};
            if name:
                opts["filename"] = name
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
            raise ValueError, 'GetFITS not defined (astropy.io.fits not found)'
        def SetFITS(self):
            """
            This method is not defined because fits in not installed.
            """
            raise ValueError, 'SetFITS not defined (astropy.io.fits not found)'

    if js9Globals['numpy']:
        def GetNumpy(self):
            """
            :rtype: numpy array

            To read a FITS file or an array from js9 into a numpy array, use
            the 'GetNumpy' method. It takes no arguments and returns the
            np array::

              >>> d.get('file')
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

            :rtype: 1 for success, 0 for failure

            After manipulating or otherwise modifying a numpy array (or making
            a new one), you can display it in js9 using the 'SetNumpy' method,
            which takes the array as its first argument::

              >>> d.SetNumpy(arr)
              1

            A return value of 1 indicates that js9 was contacted successfully,
            while a return value of 0 indicates a failure.

            An optional second argument specifies a datatype into which the
            array will be converted before being sent to js9. This is
            important in the case where the array has datatype np.uint64,
            which is not recognized by js9::

              >>> d.SetNumpy(arru64)
              ...
              ValueError: uint64 is unsupported by JS9 (or FITS)
              >>> d.SetNumpy(arru64,dtype=np.float64)
              1

            Also note that np.int8 is sent to js9 as int16 data, np.uint32 is
            sent as int64 data, and np.float16 is sent as float32 data.
            """
            if type(arr) != numpy.ndarray:
                raise ValueError, 'requires numpy.ndarray as input'
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
            hdu = {'naxis':2, 'naxis1':w, 'naxis2':h, 'bitpix':bp, 'dmin': dmin, 'dmax': dmax, 'encoding': 'base64', 'image':encarr}
            if filename:
                hdu["filename"] = filename;
            # send encoded file to JS9 for display
            return self.Load(hdu)

    else:
        def GetNumpy(self):
            """
            This method is not defined because numpy in not installed.
            """
            raise ValueError, 'GetNumpy not defined (numpy not found)'
        def SetNumpy(self):
            """
            This method is not defined because numpy in not installed.
            """
            raise ValueError, 'SetNumpy not defined (numpy not found)'

    def Load(self, *args):
        """
        Load an image into JS9
        
        call:
        
        Load(url, opts)
        
        where:
        
        -  url: url, fitsy object, in-memory FITS, or FITS blob
        -  opts: object containing image parameters
        
        NB: In Python, you probably want to call JS9.SetFITS() or
        JS9.SetNumpy() to load a local file into JS9.

        Load a FITS file or a PNG representation file into JS9. Note that
        a relative URL is relative to the JS9 install directory.

        You also can pass an in-memory buffer containing a FITS file, or a
        string containing a base64-encoded FITS file.
        
        Finally, you can pass a fits object containing the following properties:
        
        -  naxis: number of axes in the image
        -  axis: array of image dimensions for each axis or ...
        -  naxis[n] image dimensions of each axis (naxis1, naxis2, ...)
        -  bitpix: FITS bitpix value
        -  head: object containing header keywords as properties
        -  image: typed data array containing image data (native format)
        -  dmin: data min (optional)
        -  dmax: data max (optional)
        
        To override default image parameters, pass the image opts argument:

            >>> j.Load("png/m13.png", {"scale":"linear", "colormap":"sls"})
        
        """
        return self.send({"cmd": "Load", "args": args})

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
        It is needed in certain cases where JS9.Load() returns before the 
        image data is actially loaded into the display.

        A status of "complete" means that the image is fully loaded. Other
        statuses include:
        
        -  loading: the image is in process of loading
        -  error: image did not load due to an error
        -  other: another image is loaded into this display
        -  none: no image is loaded into this display
        """
        return self.send({"cmd": "GetLoadStatus", "args": args})

    def RefreshImage(self, *args):
        """
        Re-read the image data and re-display
        
        call:
        
        RefreshImage(input)
        
        where:
        
        -  input: object, javascript array, typed array, or FITS blob
        
        This routine can be used, for example, in laboratory settings where data
        is being gathered in real-time and the JS9 display needs to be refreshed
        periodically. The first input argument can be one of the following:
        
        -  a javascript array containing raw data
        -  a typed array containing raw data
        -  a blob containing a FITS file
        -  an object containing a required image property and any of the
           following optional properties:
        
           -  naxis: number of axes in the image
           -  axis: array of image dimensions for each axis or ...
           -  naxis[n] image dimensions of each axis (naxis1, naxis2, ...)
           -  bitpix: FITS bitpix value
           -  head: object containing header keywords as properties
           -  dmin: data min (optional)
           -  dmax: data max (optional)
        
        When passing an object as input, the required image property
        containing the image data can be a javascript array or a typed data
        array. It also can contain a base64-encoded string containing an array.
        This latter can be useful when calling JS9.RefreshImage() via HTTP.
        Ordinarily, when refreshing an image, there is no need to specify the
        optional axis, bitpix, or header properties. But note that you actually
        can change these values on the fly, and JS9 will process the new data
        correctly. Also, if you do not pass dmin or dmax, they will be
        calculated by JS9.
        
        Note that you can pass a blob containing a complete FITS file to
        this routine. The blob will be passed to the underlying FITS-handler
        before being displayed. Thus, processing time is slightly greater than
        if you just pass the image data directly.
        
        The main difference between JS9.RefreshImage() and JS9.Load() is
        that the former updates the data into an existing image, while the
        latter adds a completely new image to the display.
        """
        return self.send({"cmd": "RefreshImage", "args": args})

    def CloseImage(self, *args):
        """
        Clear the image from the display and mark resources for release
        
        call:
        
        CloseImage()
        
        Each loaded image claims a non-trivial amount of memory from a finite
        amount of browser heap space. For example, the default 32-bit version of
        Google Chrome has a memory limit of approximately 500Mb. If you are
        finished viewing an image, closing it tells the browser that the image's
        memory can be freed. In principle, this is can help reduce overall
        memory usage as successive images are loaded and discarded. Note,
        however, that closing an image only provides a hint to the browser,
        since this sort of garbage collection is not directly accessible to
        JavaScript programming.
        
        Some day, all browsers will support full 64-bit addressing and this
        problem will go away ...
        """
        return self.send({"cmd": "CloseImage", "args": args})

    def GetImageData(self, *args):
        """
        Get image data and auxiliary info for the specified image
        
        call:
        
        imdata  = GetImageData(dflag) where:
        
        -  dflag: specifies whether the data should also be returned
        
        returns:
        
        -  imdata: image data object
        
        NB: In Python, you probably want to call JS9.GetFITS() or
        JS9.GetNumpy() to retrieve an image.

        The image data object contains the following information:
        
        -  id: the id of the file that was loaded into JS9
        -  file: the file or URL that was loaded into JS9
        -  fits: the FITS file associated with this image
        -  source: "fits" if a FITS file was downloaded, "fits2png" if a
           representation file was retrieved
        -  imtab: "image" for FITS images and png files, "table" for FITS
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
        is returned. In JavaScript, typed arrays are more efficient than
        ordinary JavaScript arrays, and, in this case, the returned data is
        actually just reference to the real JS9 image data (so be careful about
        changing values).

        If dflag is the string "array", a JavaScript array is returned. This
        is not a reference to the real data and will utilize additional memory,
        but the values can be manipulated safely.
        
        If dflag is the string "base64", a base64-encoded string is
        returned. Oddly, this seems to be the fastest method of transferring
        data to an external process such as Python, and, in fact, is the method
        used by the pyjs9.py interface. (The "array" method also can be used,
        but seems to be slower.)
        
        The file value can be a FITS file or a representation PNG file. The
        fits value will be the path of the FITS file associated with this
        image. For a presentation PNG file, the path generally will be relative
        to the JS9 install directory. For a normal FITS file, the path usually
        is an absolute path to the FITS file.
        
        In the Python interface, you almost certainly want to set dflag to
        "array". Doing so will serialize the data as an array instead of as an
        object, saving a considerable amount of transfer data.
        """
        return self.send({"cmd": "GetImageData", "args": args})

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
        return self.send({"cmd": "GetColormap", "args": args})

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
        return self.send({"cmd": "SetColormap", "args": args})

    def GetZoom(self, *args):
        """
        Get the image zoom factor
        
        call:
        
        zoom  = GetZoom()
        
        returns:
        
        -  zoom: floating point zoom factor
        """
        return self.send({"cmd": "GetZoom", "args": args})

    def SetZoom(self, *args):
        """
        Set the image zoom factor
        
        call:
        
        SetZoom(zoom)
        
        where:
        
        -  zoom: floating or integer zoom factor or zoom directive string
        
        The zoom directives are:
        
        -  x[n]\|X[n]: multiply the zoom by n (e.g. "x2")
        -  /[n]: divide the zoom by n (e.g. "/2")
        -  in\|In: zoom in by a factor of two
        -  out\|Out: zoom out by a factor of two
        -  toFit\|ToFit: zoom to fit image in display
        """
        return self.send({"cmd": "SetZoom", "args": args})

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
        return self.send({"cmd": "GetPan", "args": args})

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
        return self.send({"cmd": "SetPan", "args": args})

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
        return self.send({"cmd": "GetScale", "args": args})

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
        return self.send({"cmd": "SetScale", "args": args})

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
        -  isys: image system (i.e. "image")
        -  px: physical x coordinate
        -  py: physical y coordinate
        -  psys: currently selected pixel-based system (i.e. "image" or
           "physical") for the above px, py values
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
        return self.send({"cmd": "GetValPos", "args": args})

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
        -  str: string of wcs in current system ("[ra] [dec] [sys]")
        
        """
        return self.send({"cmd": "PixToWCS", "args": args})

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
        -  str: string of pixel values ("[x]" "[y]")
        """
        return self.send({"cmd": "WCSToPix", "args": args})

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
        
        Get display (screen) coordinates from image coordinates. Note that image
        coordinates are one-indexed, as per FITS conventions, while display
        coordinate are 0-indexed.
        """
        return self.send({"cmd": "ImageToDisplayPos", "args": args})

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
        return self.send({"cmd": "DisplayToImagePos", "args": args})

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
        
        Logical coordinate systems include: "physical" (defined by LTM/LTV
        keywords in a FITS header), "detector" (DTM/DTV keywords), and
        "amplifier" (ATM/ATV keywords). Physical coordinates are the most
        common. In the world of X-ray astronomy, they refer to the "zoom 1"
        coordinates of the data file.

        This routine will convert from image to logical coordinates. By default,
        the current logical coordinate system is used. You can specify a
        different logical coordinate system (assuming the appropriate keywords
        have been defined).
        """
        return self.send({"cmd": "ImageToLogicalPos", "args": args})

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
        
        Logical coordinate systems include: "physical" (defined by LTM/LTV
        keywords in a FITS header), "detector" (DTM/DTV keywords), and
        "amplifier" (ATM/ATV keywords). Physical coordinates are the most
        common. In the world of X-ray astronomy, they refer to the "zoom 1"
        coordinates of the data file.

        This routine will convert from logical to image coordinates. By default,
        the current logical coordinate system is used. You can specify a
        different logical coordinate system (assuming the appropriate keywords
        have been defined).
        """
        return self.send({"cmd": "LogicalToImagePos", "args": args})

    def GetWCSUnits(self, *args):
        """
        Get the current WCS units
        
        call:
        
        unitsstr  = GetWCSUnits()
        
        returns:
        
        -  unitstr: "pixels", "degrees" or "sexagesimal"
        """
        return self.send({"cmd": "GetWCSUnits", "args": args})

    def SetWCSUnits(self, *args):
        """
        Set the current WCS units
        
        call:
        
        SetWCSUnits(unitsstr)
        
        where:
        
        -  unitstr: "pixels", "degrees" or "sexagesimal"
        
        Set the current WCS units.
        """
        return self.send({"cmd": "SetWCSUnits", "args": args})

    def GetWCSSys(self, *args):
        """
        Get the current World Coordinate System
        
        call:
        
        sysstr  = GetWCSSys()
        
        returns:
        
        -  sysstr: current World Coordinate System ("FK4", "FK5", "ICRS",
           "galactic", "ecliptic", "image", or "physical");
        """
        return self.send({"cmd": "GetWCSSys", "args": args})

    def SetWCSSys(self, *args):
        """
        Set the current World Coordinate System
        
        call:
        
        SetWCSSys(sysstr)
        
        where:
        
        -  sysstr: World Coordinate System ("FK4", "FK5", "ICRS",
           "galactic", "ecliptic", "image", or "physical")
        
        Set current WCS system. The WCS systems are available only if WCS
        information is contained in the FITS header. Also note that "physical"
        coordinates are the coordinates tied to the original file. They are
        mainly used in X-ray astronomy where individually detected photon events
        are binned into an image, possibly using a blocking factor. For optical
        images, image and physical coordinate usually are identical.
        """
        return self.send({"cmd": "SetWCSSys", "args": args})

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
        
        This routine creates a new named shape layer. You can then, add, change,
        and remove shapes in this layer using the routines below. The catalogs
        displayed by the Catalog plugin are examples of separate shape layers.
        The optional opts parameter allows you to specify default options
        for this layer. You can set a default for any property needed by your
        shape layer. See JS9.Regions.opts in js9.js for an example of the
        default options for the regions layer.
        """
        return self.send({"cmd": "NewShapeLayer", "args": args})

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
        return self.send({"cmd": "ShowShapeLayer", "args": args})

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
        
        The sarr argument can be a shape ("annulus", "box", "circle",
        "ellipse", "point", "polygon", "text"), a single shape object, or an
        array of shape objects. Shape objects contain one or more properties, of
        which the most important are:
        
        -  shape: "annulus", "box", "circle", "ellipse", "point", "polygon",
           "text" [REQUIRED]
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
        return self.send({"cmd": "AddShapes", "args": args})

    def RemoveShapes(self, *args):
        """
        Remove one or more shapes from the specified shape layer
        
        call:
        
        RemoveShapes(layer, shapes)
        
        where:
        
        -  layer: name of layer
        -  shapes: which shapes to remove
        """
        return self.send({"cmd": "RemoveShapes", "args": args})

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
        -  mode: "add", "remove", or "change"
        -  shape: region shape ("annulus", "box", "circle", "ellipse",
           "point", "polygon", "text")
        -  tags: comma delimited list of region tags (e.g., "source",
           "include")
        -  color: region color
        -  x,y: image coordinates of region
        -  size: object containing width and height for box region
        -  radius: radius value for circle region
        -  radii: array of radii for annulus region
        -  eradius: object containing x and y radii for ellipse regions
        -  pts: array of objects containing x and y positions, for polygons
        -  angle: angle in degrees for box and ellipse regions
        """
        return self.send({"cmd": "GetShapes", "args": args})

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
        change the shape itself (e.g. from "box" to "circle").
        """
        return self.send({"cmd": "ChangeShapes", "args": args})

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
        
        The rarr argument can be a region shape ("annulus", "box", "circle",
        "ellipse", "point", "polygon", "text"), a single region object, or an
        array of region objects. Region objects contain one or more properties,
        of which the most important are:
        
        -  shape: "annulus", "box", "circle", "ellipse", "point", "polygon",
           "text" [REQUIRED]
        -  x: image x position
        -  y: image y position
        -  lcs: object containing logical x, y and sys (e.g. "physical")
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
        return self.send({"cmd": "AddRegions", "args": args})

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
        -  mode: "add", "remove" or "change"
        -  shape: region shape ("annulus", "box", "circle", "ellipse",
           "point", "polygon", "text")
        -  tags: comma delimited list of region tags (e.g., "source",
           "include")
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
        -  wcssys: wcs system (e.g. "FK5")
        -  imstr: region string in image or physical coordinates
        -  imsys: image system ("image" or "physical")
        """
        return self.send({"cmd": "GetRegions", "args": args})

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
        change the shape itself (e.g. from "box" to "circle"). See
        js9onchange.html for examples of how to use this routine.
        """
        return self.send({"cmd": "ChangeRegions", "args": args})

    def RemoveRegions(self, *args):
        """
        Remove one or more regions from the region layer
        
        call:
        
        RemoveRegions(regions)
        
        where:
        
        -  regions: which regions to remove
        """
        return self.send({"cmd": "RemoveRegions", "args": args})

    def RunAnalysis(self, *args):
        """
        Run a simple server-side analysis task
        
        call:
        
        RunAnalysis(name, parr)
        
        where:
        
        -  name: name of analysis tool
        -  parr: optional array of macro-expansion options for command line
        
        The JS9.RunAnalysis() routine is used to execute a server-side analysis
        task and return the results for further processing within JS9.

        The default processing will display "text" in a new light window.
        If the return type is "plot", the results are assumed to be in flot
        format and will be plotted.

        The optional parr array of parameters is passed to the JS9 analysis
        macro expander so that values can be added to the command line. The
        array is in jQuery name/value serialized object format, which is
        described here:
        
                http://api.jquery.com/serializeArray/
        
        """
        return self.send({"cmd": "RunAnalysis", "args": args})

    def Print(self, *args):
        """
        """
        return self.send({"cmd": "Print", "args": args})

    def DisplayHelp(self, *args):
        """
        Display help in a light window
        
        call:
        
        DisplayHelp(name)
        
        where:
        
        -  name: name of a help file or url of a Web site to display
        
        The help file names are the property names in JS9.helpOpts (e.g., "user"
        for the user page, "install" for the install page, etc.). Alternatively,
        you can specify an arbitrary URL to display (just because).
        """
        return self.send({"cmd": "DisplayHelp", "args": args})

    def analysis(self, *args):
        """
        run/list analysis for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string.
        """
        return self.send({"cmd": "analysis", "args": args})

    def colormap(self, *args):
        """
        set/get colormap for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string: 'colormap contrast bias'
        """
        return self.send({"cmd": "colormap", "args": args})

    def cmap(self, *args):
        """
        set/get colormap for current image (alias)

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string: 'colormap contrast bias'
        """
        return self.send({"cmd": "cmap", "args": args})

    def colormaps(self, *args):
        """
        get list of available colormaps

        No setter routine is provided.
        Returned results are of type string: 'grey, red, ...'
        """
        return self.send({"cmd": "colormaps", "args": args})

    def image(self, *args):
        """
        get name of currently loaded image or display specified image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string.
        """
        return self.send({"cmd": "image", "args": args})

    def images(self, *args):
        """
        get list of currently loaded images

        No setter routine is provided.
        Returned results are of type string.
        """
        return self.send({"cmd": "images", "args": args})

    def load(self, *args):
        """
        load image(s)

        No getter routine is provided.
        """
        return self.send({"cmd": "load", "args": args})

    def pan(self, *args):
        """
        set/get pan location for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string: 'x y'
        """
        return self.send({"cmd": "pan", "args": args})

    def regions(self, *args):
        """
        add region to current image or list all regions

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string.
        """
        return self.send({"cmd": "regions", "args": args})

    def region(self, *args):
        """
        add region to current image or list all regions (alias)

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string.
        """
        return self.send({"cmd": "region", "args": args})

    def scale(self, *args):
        """
        set/get scaling for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string: 'scale scalemin scalemax'
        """
        return self.send({"cmd": "scale", "args": args})

    def scales(self, *args):
        """
        get list of available scales

        No setter routine is provided.
        Returned results are of type string: 'linear, log, ...'
        """
        return self.send({"cmd": "scales", "args": args})

    def wcssys(self, *args):
        """
        set/get wcs system for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string.
        """
        return self.send({"cmd": "wcssys", "args": args})

    def wcsu(self, *args):
        """
        set/get wcs units used for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are of type string.
        """
        return self.send({"cmd": "wcsu", "args": args})

    def wcssystems(self, *args):
        """
        get list of available wcs systems

        No setter routine is provided.
        Returned results are of type string: 'FK4, FK5, ...'
        """
        return self.send({"cmd": "wcssystems", "args": args})

    def wcsunits(self, *args):
        """
        get list of available wcs units

        No setter routine is provided.
        Returned results are of type string: 'degrees, ...'
        """
        return self.send({"cmd": "wcsunits", "args": args})

    def zoom(self, *args):
        """
        set/get zoom for current image

        This is a commmand-style routine, easier to type than the full routine
        at the expense of some flexibility:
          - with no arguments, the getter is called to retrieve current values.
          - with arguments, the setter is called to set current values. 

        Returned results are type integer or float.
        """
        return self.send({"cmd": "zoom", "args": args})
