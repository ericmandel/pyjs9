"""
pyjs9.py: connects Python and JS9 via the JS9 (back-end) helper
"""
from __future__ import print_function

import time
import json
import base64
import logging
from traceback import format_exc
from threading import Condition
from io import BytesIO

import requests

__all__ = ['JS9', 'js9Globals']

"""
pyjs9.py connects Python and JS9 via the JS9 (back-end) helper

- The JS9 class constructor connects to a single JS9 instance in a web page.
- The JS9 object supports the JS9 Public API and a shorter command-line syntax.
- See: http://js9.si.edu/js9/help/publicapi.html
- Send/retrieve numpy arrays and astropy (or pyfits) hdulists to/from js9.
- Use python-socketio for fast, persistent connections to the JS9 back-end
"""

# pyjs9 version
__version__ = '3.8'

# try to be a little bit neat with global parameters
js9Globals = {}

js9Globals['version'] = __version__

# what sort of fits verification gets done on SetFITS() output?
# see astropy documentation on write method
js9Globals['output_verify'] = 'ignore'

# retrieve image data from JS9 as an array or as base64 encoded string
# in the early days, base64 seemed to be faster
# js9Globals['retrieveAs'] = 'base64'
# array allows us to deal with larger images
js9Globals['retrieveAs'] = 'array'

# how to turn on logging at most verbose level:
# logging.basicConfig(level=logging.DEBUG)

# load fits, if available
try:
    from astropy.io import fits
    js9Globals['fits'] = 1
except ImportError:
    try:
        import pyfits as fits
        if fits.__version__ >= '2.2':
            js9Globals['fits'] = 2
        else:
            js9Globals['fits'] = 0
    except ImportError:
        js9Globals['fits'] = 0

# load numpy, if available
try:
    import numpy
    js9Globals['numpy'] = 1
except ImportError:
    js9Globals['numpy'] = 0

# load socket.io, if available
try:
    import socketio
    logging.info('set socketio transport')
    js9Globals['transport'] = 'socketio'
    js9Globals['wait'] = 10
except ImportError:
    logging.info('no python-socketio, use html transport')
    js9Globals['transport'] = 'html'
    js9Globals['wait'] = 0

# utilities
def _decode_list(data):
    rv = []
    for item in data:
        if isinstance(item, list):
            item = _decode_list(item)
        elif isinstance(item, dict):
            item = _decode_dict(item)
        rv.append(item)
    return rv


def _decode_dict(data):
    rv = {}
    for key, value in data.items():
        if isinstance(value, list):
            value = _decode_list(value)
        elif isinstance(value, dict):
            value = _decode_dict(value)
        rv[key] = value
    return rv


# numpy-dependent routines
if js9Globals['numpy']:
    def _bp2np(bitpix):  # pylint: disable=too-many-return-statements
        """
        Convert FITS bitpix to numpy datatype
        """
        if bitpix == 8:
            return numpy.uint8
        if bitpix == 16:
            return numpy.int16
        if bitpix == 32:
            return numpy.int32
        if bitpix == 64:
            return numpy.int64
        if bitpix == -32:
            return numpy.float32
        if bitpix == -64:
            return numpy.float64
        if bitpix == -16:
            return numpy.uint16
        raise ValueError('unsupported bitpix: %d' % bitpix)

    _NP_TYPE_MAP = (
        # pylint: disable=bad-whitespace
        (numpy.uint8  , numpy.uint8,  ),
        (numpy.int8   , numpy.int16,  ),
        (numpy.uint16 , numpy.uint16, ),
        (numpy.int16  , numpy.int16,  ),
        (numpy.int32  , numpy.int32,  ),
        (numpy.uint32 , numpy.int64,  ),
        (numpy.int64  , numpy.int64,  ),
        (numpy.float16, numpy.float32,),
        (numpy.float32, numpy.float32,),
        (numpy.float64, numpy.float64,),
    )

    def _cvt2np(ndarr: numpy.ndarray):
        # NOTE cvt2np may be merged into np2bp
        dtype = ndarr.dtype
        for t in _NP_TYPE_MAP:
            if numpy.issubdtype(dtype, t[0]):
                return ndarr.astype(t[1])
        return ndarr

    def _np2bp(dtype):  # pylint: disable=too-many-return-statements
        """
        Convert numpy datatype to FITS bitpix
        """
        if dtype == numpy.uint8:
            return 8
        if dtype == numpy.int16:
            return 16
        if dtype == numpy.int32:
            return 32
        if dtype == numpy.int64:
            return 64
        if dtype == numpy.float32:
            return -32
        if dtype == numpy.float64:
            return -64
        if dtype == numpy.uint16:
            return -16
        raise ValueError('unsupported dtype: %s' % dtype)

    def _bp2py(bitpix):  # pylint: disable=too-many-return-statements
        """
        Convert FITS bitpix to python datatype
        """
        if bitpix == 8:
            return 'B'
        if bitpix == 16:
            return 'h'
        if bitpix == 32:
            return 'l'
        if bitpix == 64:
            return 'q'
        if bitpix == -32:
            return 'f'
        if bitpix == -64:
            return 'd'
        if bitpix == -16:
            return 'H'
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
            s = base64.decodebytes(im['data'].encode())[0:dlen]
            if d > 1:
                arr = numpy.frombuffer(s, dtype=dtype).reshape((d, h, w))
            else:
                arr = numpy.frombuffer(s, dtype=dtype).reshape((h, w))
        else:
            raise ValueError('unknown retrieveAs type for GetImageData()')
        return arr


class JS9:
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

    def __init__(self, host='http://localhost:2718', id='JS9', multi=False, pageid=None, maxtries=5, delay=1, debug=False):  # pylint: disable=redefined-builtin, too-many-arguments, line-too-long
        """
        :param host: host[:port] (def: 'http://localhost:2718')
        :param id: the JS9 display id (def: 'JS9')

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
        if c <= s:
            host += ':2718'
        if s < 0:
            host = 'http://' + host
        self.__dict__['host'] = host
        self.__dict__['multi'] = multi
        self.__dict__['pageid'] = pageid
        # open socket.io connection, if necessary
        if js9Globals['transport'] == 'socketio':
            try:
                if debug:
                    self.sockio = socketio.Client(logger=True,
                                                  engineio_logger=True)
                else:
                    self.sockio = socketio.Client()
                self.sockio.connect(host)
            except Exception as e:  # pylint: disable=broad-except
                logging.warning('socketio connect failed: %s, using html', e)
                js9Globals['transport'] = 'html'
        self._block_cb = None
        # wait for connect be ready, but success doesn't really matter here
        tries = 0
        while tries < maxtries:
            try:
                self._alive()
            # check for instance error, else sleep and try again
            except Exception as e:  # pylint: disable=broad-except
                if 'JS9 instance(s) found with id' in str(e):
                    print(e)
                    break
                time.sleep(delay)
                tries = tries + 1
            else:
                break

    def __setitem__(self, itemname, value):
        """
        An internal routine to process some assignments specially
        """
        self.__dict__[itemname] = value
        if itemname in ('host', 'id',):
            self._alive()

    def _alive(self):
        """
        An internal routine to send a test message to the helper
        """
        self.send(None, msg='alive')

    def sockioCB(self, *args):
        """
        Internal routine
        """
        logging.debug('socketio callback, args: %s', args)
        self.__dict__['sockioResult'] = args[0]
        self._block_cb.acquire()
        self._block_cb.notify()
        self._block_cb.release()

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
        obj['id'] = self.__dict__['id']
        obj['multi'] = self.__dict__['multi']
        if self.__dict__['pageid'] is not None:
            obj['pageid'] = self.__dict__['pageid']

        if js9Globals['transport'] == 'html': # pylint: disable=no-else-return
            host = self.__dict__['host']
            try:
                url = requests.post(host + '/' + msg, json=obj)
            except IOError as e:
                raise IOError('Cannot connect to {0}: {1}'.format(host, e))
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
                if isinstance(res, str):
                    res = res.strip()
            return res
        else:
            self.__dict__['sockioResult'] = ''
            self._block_cb = Condition()
            self._block_cb.acquire()
            self.sockio.emit('msg', obj, callback=self.sockioCB)
            self._block_cb.wait(timeout=js9Globals['wait'])
            self._block_cb.release()
#            self.sockio.wait_for_callbacks(seconds=js9Globals['wait'])
            if self.__dict__['sockioResult'] and \
               isinstance(self.__dict__['sockioResult'], str) and \
               'ERROR:' in self.__dict__['sockioResult']:
                raise ValueError(self.__dict__['sockioResult'])
            return self.__dict__['sockioResult']

    def close(self):
        """
        Close the socketio connection and disconnect from the server
        """
        if js9Globals['transport'] == 'socketio':
            try:
                self.sockio.disconnect()
            except Exception as e:  # pylint: disable=broad-except
                logging.error('socketio close failed: %s', e)

    if js9Globals['fits']:
        def GetFITS(self):
            """
            :rtype: fits hdulist

            To read FITS data or a raw array from js9 into fits, use the
            'GetFITS' method. It takes no args and returns an hdu list::

              >>> hdul = j.GetFITS()
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
            # if the image is too large, we can back get an empty string
            if im == '':
                raise ValueError('GetImageData failed: image too large for Python transport?')
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
            if not isinstance(hdul, fits.HDUList):
                if js9Globals['fits'] == 1:
                    raise ValueError('requires astropy.HDUList as input')
                raise ValueError('requires pyfits.HDUList as input')
            # in-memory string
            memstr = BytesIO()
            # write fits to memory string
            hdul.writeto(memstr, output_verify=js9Globals['output_verify'])
            # get memory string as an encoded string
            encstr = base64.b64encode(memstr.getvalue()).decode()
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
        @staticmethod
        def GetFITS():
            """
            This method is not defined because fits in not installed.
            """
            raise ValueError('GetFITS not defined (astropy.io.fits not found)')

        @staticmethod
        def SetFITS():
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
              >>> arr = j.GetNumpy()
              >>> arr.shape
              (1024, 1024)
              >>> arr.dtype
              dtype('float32')
              >>> arr.max()
              51.0
            """
            # get image data from JS9
            im = self.GetImageData(js9Globals['retrieveAs'])
            # if the image is too large, we can get back an empty string
            if im == '':
                raise ValueError('GetImageData failed: image too large for Python transport?')
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
            if not isinstance(arr, numpy.ndarray):
                raise ValueError('requires numpy.ndarray as input')
            if dtype and dtype != arr.dtype:
                narr = arr.astype(dtype)
            else:
                narr = _cvt2np(arr)

            if not narr.flags['C_CONTIGUOUS']:
                narr = numpy.ascontiguousarray(narr)
            # parameters to pass back to JS9
            bp = _np2bp(narr.dtype)
            (h, w) = narr.shape
            dmin = narr.min().tolist()
            dmax = narr.max().tolist()
            # base64-encode numpy array in native format
            encarr = base64.b64encode(narr.tostring()).decode()
            # create object to send to JS9 containing encoded array
            hdu = {'naxis': 2, 'naxis1': w, 'naxis2': h, 'bitpix': bp,
                   'dmin': dmin, 'dmax': dmax, 'encoding': 'base64',
                   'image': encarr}
            if filename:
                hdu['filename'] = filename
            # send encoded file to JS9 for display
            return self.Load(hdu)

    else:
        @staticmethod
        def GetNumpy():
            """
            This method is not defined because numpy in not installed.
            """
            raise ValueError('GetNumpy not defined (numpy not found)')

        @staticmethod
        def SetNumpy():
            """
            This method is not defined because numpy in not installed.
            """
            raise ValueError('SetNumpy not defined (numpy not found)')

    def Load(self, *args):
        """
        Load an image into JS9

        call:

        JS9.Load(url, opts)

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

    def LoadWindow(self, *args):
        """
        Load an image into a light window or a new (separate) window

        call:

        JS9.LoadWindow(url, opts, type, html, winopts)

        where:

        -  url: remote URL image to load
        -  opts: object containing image parameters
        -  type: "light" or "new"
        -  html: html for the new page (def: menubar, image, colorbar)
        -  winopts: for "light", optional dhtml window options

        returns:

        -  id: the id of the JS9 display div

        This routine will load an image into a light-weight window or an
        entirely new window. The url and opts arguments are identical to
        the standard JS9.Load() call, except that opts can contain:

        -  id: string specifying the id of the JS9 display being created:
           if no id is specified, a unique id is generated
        -  clone: the id of a display to clone when creating a light window:
           the menubar and colorbar will be created if and only if they are
           present in the cloned display

        The type argument determines whether to create a light-weight
        window ("light", the default) or a new separate window ("new").

        You can use the html argument to supply different web page elements
        for the window. Furthermore, if you create a light window, a default
        set of DynamicDrive dhtmlwindow parameters will be used to make the
        window the correct size for the default html:

        "width=512px,height=542px,center=1,resize=1,scrolling=1"

        You can supply your own parameters for the new dhtmlwindow using the
        winOpts argument. See the Dynamic Drive web site:

        http://www.dynamicdrive.com/dynamicindex8/dhtmlwindow/index.htm

        for more information about their light-weight window.

        To create a new light window without loading an image, use:

          >>>> JS9.LoadWindow("", "", "light");

        """
        return self.send({'cmd': 'LoadWindow', 'args': args})

    def LoadProxy(self, *args):
        """
        Load an FITS image link into JS9 using a proxy server

        call:

        JS9.LoadProxy(url, opts)

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

          >>> j.LoadProxy('http://hea-www.cfa.harvard.edu/~eric/coma.fits',
                          {'scale':'linear', 'colormap':'sls'})

        If an onload callback function is specified in opts, it will be called
        after the image is loaded:

          >>> j.LoadProxy('http://hea-www.cfa.harvard.edu/~eric/coma.fits',
                          {'scale': 'linear', 'onload': func})
        """
        return self.send({'cmd': 'LoadProxy', 'args': args})

    def GetStatus(self, *args):
        """
        Get Processing Status

        call:

        status  = JS9.GetStatus(type, id)

        where:

        -  type: the type of status
        -  id: the id of the file that was loaded into JS9

        returns:

        -  status: status of the processing

        This routine returns the status of one of the following specified
        asynchronous processing types: "Load", "CreateMosaic",
        "DisplaySection", "LoadCatalog", "LoadRegions", "ReprojectData",
        "RotateData", "RunAnalysis".

        A status of "complete" means that the image is fully processed. Other
        statuses include:

        -  processing: the image is being processed
        -  loading: the image is in process of loading ("Load" only)
        -  error: image did not load due to an error
        -  other: another image is loaded into this display
        -  none: no image is loaded into this display
        """
        return self.send({'cmd': 'GetStatus', 'args': args})

    def GetLoadStatus(self, *args):
        """
        Get Load Status

        call:

        status  = JS9.GetLoadStatus(id)

        where:

        -  id: the id of the file that was loaded into JS9

        returns:

        -  status: status of the load

        This routine returns the status of the load of this image.
        Provided for backward compatibility, it simply calls the more general
        GetStatus() routine with "Load" as the first argument.

        A status of 'complete' means that the image is fully loaded. Other
        statuses include:

        -  loading: the image is in process of loading
        -  error: image did not load due to an error
        -  other: another image is loaded into this display
        -  none: no image is loaded into this display
        """
        return self.send({'cmd': 'GetLoadStatus', 'args': args})

    def DisplayImage(self, *args):
        """
        Display an image

        call:

        JS9.RefreshImage(step)

        where:

        -  step: starting step to take when displaying the image

        The display steps are: "colors" (remake colors when cmap has changed),
        "scaled" (rescale data values), "primary" (convert scaled data values
        to color values), and "display" (write color values to the web page).

        The default step is "primary", which displays the image without
        recalculating color data, scaled data, etc. This generally is what you
        want, unless you have changed parameter(s) used in a prior step.
        """
        return self.send({'cmd': 'DisplayImage', 'args': args})

    def RefreshImage(self, *args):
        """
        Re-read the image data and re-display

        call:

        JS9.RefreshImage(input)

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

        JS9.CloseImage()

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

        imdata  = JS9.GetImageData(dflag)

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

    def DisplayPlugin(self, *args):
        """
        Display plugin in a light window

        call:

        JS9.DisplayPlugin(plugin)

        where:

        - plugin: name of the plugin

        Toggle the light-window display of the named plugin, as is done
        by the View and Analysis menus. That is, if the plugin is not
        visible, make it visible. If the plugin is visible, hide it.

        You can supply the full class and plugin name or just the name, using
        exact case or lower case, e.g.:

        -  JS9Panner or panner
        -  JS9Magnifier or magnifier
        -  JS9Info or info
        -  JS9Console or console
        -  DataSourcesArchivesCatalogs or archivescatalogs
        -  FitsBinning or binning
        -  ImExamEncEnergy or encenergy
        -  ImExamPxTabl or pxtabl
        -  ImExamRadialProj or radialproj
        -  ImExamHistogram or histogram
        -  ImExamRegionStats or regionstats
        -  ImExamXProj or xproj
        -  ImExamYProj or yproj
        -  ImExam3dPlot or 3dplot
        -  ImExamContours or contours

        As with plugins in the View and Analysis menus, this routine does
        nothing if the plugin is explicitly defined on the web page.
        """
        return self.send({'cmd': 'DisplayPlugin', 'args': args})

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

    def DisplaySection(self, *args):
        """
        Extract and display a section of a FITS file

        call:

        JS9.DisplaySection(opts)

        where:

        - opts: object containing options

        This routine allows you to extract and display a section of FITS file.
        The opts object contains properties specifying how to generate and
        display the section:

         - xcen: x center of the section in file (physical) coords (required)
         - ycen: y center of the section in file (physical) coords (required)
         - xdim: x dimension of section to extract before binning
         - ydim: y dimension of section to extract before binning
         - bin:  bin factor to apply after extracting the section
         - filter: for tables, row/event filter to apply when extracting a
            section
         - separate: if true, display as a separate image (def: to update
            the current image)

        All properties are optional: by default, the routine will extract a bin
        1 image from the center of the file.

        For example, if an image has dimensions 4096 x 4096, then specifying:

         - center: 1024, 1024
         - dimensions: 1024, 1024
         - bin: 2

        will bin the upper left 1024 x 1024 section of the image by 2 to
        produce a 512 x 512 image.  Note that 0,0 can be used to specify the
        file center.

        Table filtering allows you  to select rows from an FITS binary table
        (e.g., an X-ray event list) by checking each row against an expression
        involving the columns in the table. When a table is filtered, only
        valid rows satisfying these expressions are used to make the image.

        A filter expression consists of an arithmetic or logical operation
        involving one or more column values from a table. Columns can be
        compared to other columns or to numeric constants. Standard JavaScript
        math functions can be applied to columns. JavaScript (or C) semantics
        are used when constructing expressions, with the usual precedence and
        associativity rules holding sway:

          Operator                                Associativity
          --------                                -------------
          ()                                      left to right
          !  (bitwise not) - (unary minus)        right to left
          *  /                                    left to right
          +  -                                    left to right
          < <= > >=                               left to right
          == !=                                   left to right
          &  (bitwise and)                        left to right
          ^  (bitwise exclusive or)               left to right
          |  (bitwise inclusive or)               left to right
          && (logical and)                        left to right
          || (logical or)                         left to right
          =                                       right to left

        For example, if energy and pha are columns in a table, then the
        following are valid expressions:

          pha > 1
          energy == pha
          pha > 1 && energy <= 2
          max(pha,energy) >= 2.5

        NB: JS9 uses cfitsio by default (you can, but should not, use the
        deprecated fitsy.js), and therefore follows cfitsio filtering
        conventions, which are documented in:

        https://heasarc.gsfc.nasa.gov/docs/software/fitsio/c/c_user/node97.html
        """
        return self.send({'cmd': 'DisplaySection', 'args': args})

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

        calling sequences:

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
          >>> JS9.BlendImage(blend, opacity) # set/modify blend mode or opacity
        """
        return self.send({'cmd': 'BlendImage', 'args': args})

    def SyncImages(self, *args):
        """
        Synchronize operations between two or more images

        call:

        JS9.SyncImages([ops], [images], [opts])  # set up synchronization
        JS9.SyncImages(true||false)              # turn on/off synchronization

        where:

        - ops: array of operations on which to sync
        - images: array of images to sync with this image
        - opts: options for sync'ing

        Synchronize two or more images, so that when an operation is performed
        on one image, it also is performed on the other(s). For example, when
        the colormap or scale is changed on an image, it also is changed on
        the sync'ed images. Or, when a region is created, moved, resized, or
        removed on an image, the same happens on the sync'ed images.

        When the SyncImages() call is invoked, the current image is
        configured to synchronize the specified images.  In addition, if
        the reciprocate property is set in the opts object (see below),
        the other images are also configured to synchronize one another (as
        well as the current image). Once configuration is complete, a sync
        command is executed immediately. If the current image already
        displays one or more regions, these will be created in the target
        images.

        The operations that can be specified for sync'ing are:
        "colormap", "pan", "regions", "scale", "wcs", "zoom", "contrastbias".
        If no array is specified, the default array in JS9.globalOpts.syncOps
        is used.

        Images to synchronize can be specified as an array of image handles or
        image ids. If no array is specified, all currently displayed images
        are sync'ed.

        The optional opts object can contain:

        - reciprocate: boolean determining whether images sync one another
        - reverse: boolean to reverse this image and target images (def: false)

        If the opts object is not specified, the default value of
        reciprocate is the value of the JS9.globalOpts.syncReciprocate
        property.

        Examples:

        >>> # the current image will sync all operations for all images
        >>> # sync reciprocally, so that changing any image syncs the others
        >>> SyncImages()

        >>> # current image will sync specified ops for foo1.fits,foo2.fits:
        >>> SyncImages(["scale", "colormap"], ["foo1.fits", "foo2.fits"])

        >>> # the current image will sync two images with default ops,
        >>> # but the two images themselves will not sync images reciprocally
        >>> SyncImages(null, ["foo1.fits", "foo2.fits"], {reciprocate: false});

        Note that if the pan operation syncs two images having differently
        sized fields of view, the smaller image will stop panning when it
        reaches its edge, rather than displaying a blank field.

        You can turn on/off syncing for a given image by specifying a single
        boolean argument:

        >>> # turn off sync'ing temporarily
        >>> SyncImages(false);

        This is different from unsync'ing in that you can turn sync'ing back
        on without having to re-sync the images.
        """
        return self.send({'cmd': 'SyncImages', 'args': args})

    def UnsyncImages(self, *args):
        """
        Unsynchronize two or more previously synchronized images

        call:

        JS9.UnsyncImages([ops], [images], [opts])  # clear synchronization

        where:

        - ops: array of operations to unsync
        - images: array of images to unsync with this image
        - opts: options for unsync'ing

        Unsynchronize previously sync'ed images.

        The operations that can be specified for unsync'ing are:
        "colormap", "pan", "regions", "scale", "wcs", "zoom", "contrastbias".
        If no array is specified, the default array in JS9.globalOpts.syncOps is
        used. Thus, you can turn off sync'ing for specified operations, while
        leaving others to be sync'ed.

        Images to be unsync'ed can be specified as an array of image handles or
        image ids. If no array is specified, all currently displayed images
        are unsync'ed.

        The optional opts object can contain:

        - reciprocate: boolean determining whether images sync one another
        - reverse: boolean to reverse this image and target images (def: false)

        If the opts object is not specified, the default is to reciprocate based
        on the value of the JS9.globalOpts.syncReciprocate property.

        Examples:

        >>> # this image won't sync on scale for foo1.fits and foo2.fits,
        >>> # and they also will stop sync'ing
        UnsyncImages(["scale"], ["foo1.fits", "foo2.fits"])

        >>> # this image will still sync foo1.fits and foo2.fits, but
        >>> # foo1.fits and foo2.fits will no longer sync this image:
        UnsyncImages(null, ["foo1.fits", "foo2.fits"],
                     {reverse: true, reciprocal: false})
        """
        return self.send({'cmd': 'UnsyncImages', 'args': args})

    def MaskImage(self, *args):
        """
        Mask an image using values in another image

        call:

        JS9.MaskImage(image, opts)

        calling sequences:

        JS9.MaskImage()              # return current mask params
        JS9.MaskImage(true||false)   # turn on/off masking
        JS9.MaskImage(image, opts)   # set mask and optionally, its params
        JS9.MaskImage(opts)          # set mask params

        where:

        - image: image handle or image id to use as a mask
        - opts: optional mask properties

        and where the mask properties are:

        - mode: "mask", "opacity", or "overlay"
        - value: mask value that triggers masking (def: 0) for "mask" mode
        - invert: whether to invert the mask (def: false) for "mask" mode
        - def: object containing default RGBA values for "overlay" mode
        - opacity: opacity when masking (def: 0, range 0 to 1) for both mode

        The pixel values in one image can be used to mask the pixels in
        another image if the two images have the same image dimensions.
        The type of masking depends on the mode: "overlay" (default) or "mask".

        For "mask" mode, if the value of a pixel in the mask is less than or
        equal to the value property, the opacity of the displayed pixel
        is set to the opacity property. You can also invert the mask
        using the invert property. In effect, this mode displays only
        the image pixels "covered" by a mask.

        For "opacity" mode, each image pixel is assigned an opacity equal
        to the value of the mask pixel (whose values are assumed to range
        from 0 to 1.)

        For "overlay" mode, if the mask pixel has a non-zero alpha, its color
        is blended with the image pixel using source-atop composition.
        Otherwise, the image pixel color alone is used in the display.
        This is one way you can display a mask overlay on top of an image.
        A static colormap is usually used in conjunction with an overlay
        mask, since pixel values not explicitly assigned a color are
        transparent.  Note that, when blending a mask and image pixel, the
        global mask opacity and the individual pixel opacity are multiplied to
        get the final pixel opacity.

        To set up a mask initially, call the routine with an already-loaded
        mask image as the first parameter, and an optional opts object as the
        second parameter:

        >>> # default is "overlay"
        >>> JS9.ImageMask("casa_mask.fits");
        >>> JS9.ImageMask("casa_mask.fits", {mode: "overlay"});

        >>> # "mask" mode: set lower threshold for masking and masked opacity
        >>> JS9.ImageMask("mask.fits",{"mode":"mask","value":5,"opacity":0.2});

        You can change the mask parameters at any time:

        >>> JS9.ImageMask({value: 2, opacity: 0});

        or temporarily turn off and on the mask:

        >>> JS9.ImageMask(false);
        >>> ...
        >>> JS9.ImageMask(true);
        """
        return self.send({'cmd': 'MaskImage', 'args': args})

    def BlendDisplay(self, *args):
        """
        Set global blend mode for specified display

        call:

        mode = JS9.BlendDisplay(True|False)

        returns:

        - mode: current image blend mode

        This routine will turn on/off the global image blend mode for the
        specified display. If no argument is specified, it returns the current
        blend mode.
        """
        return self.send({'cmd': 'BlendDisplay', 'args': args})

    def GetColormap(self, *args):
        """
        Get the image colormap

        call:

        cmap  = JS9.GetColormap()

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

        JS9.SetColormap(cmap, [contrast, bias])

        calling sequences:

        JS9.SetColormap(colormap)
        JS9.SetColormap(colormap, contrast, bias)
        JS9.SetColormap(colormap, staticOpts)
        JS9.SetColormap(contrast, bias)
        JS9.SetColormap(staticOpts)

        where:

        -  cmap: colormap name
        -  contrast: contrast value (range: 0 to 10)
        -  bias: bias value (range 0 to 1)
        -  staticOpts: static colormap opts

        Set the current colormap, contrast/bias, or both. This call takes one
        (colormap), two (contrast, bias) or three (colormap, contrast, bias)
        arguments. It also takes the following single arguments:

        - rgb: toggle RGB mode
        - invert: toggle inversion of the colormap
        - reset: reset contrast, bias, and invert values
        - staticOpts: opts for a static colormap

        The staticOpts argument is an array of parameters to change
        in a static colormap. Each parameter can take one of two forms:

        - [color, min, max]
        - [color, opacity|alpha]
        - [color, true|false]

        The color parameter must match one of the colors specified when
        the static colormap was created. The min and max properties replace
        the originally specified min and max values. Specifying a number
        between 0 and 1 (inclusive) will change the opacity, while specifying
        a number greater than 1 will change the alpha (i.e., opacity * 255).
        Specifying true or false will set or unset the active flag for that
        color, i.e.  it will turn on or off use of that color. When turned off,
        the pixels in that range will be transparent. For example:

        >>> SetColormap '[["red", 0.5], ["green", true], ["blue", false]]'

        sets the opacity of red pixels to 0.5, turns on the green pixels,
        and turns off the blue pixels in the currently active static colormap.
        """
        return self.send({'cmd': 'SetColormap', 'args': args})

    def SaveColormap(self, *args):
        """
        Save colormap(s)

        calling sequences:

        JS9.SaveColormap()                 # save current colormap to "js9.cmap"
        JS9.SaveColormap(fname)            # save current colormap to fname
        JS9.SaveColormap(cmapArray)        # save array of ccmaps to "js9.cmap"
        JS9.SaveColormap(fname, cmapArray) # save array of cmaps to fname

        where:

        - fname: output file name
        - cmapArray: optional array of colormap names to save

        As shown by the calling sequences above, you can use this routine to
        save either the current colormap or a list of colormaps taken from the
        specified array. You also can choose to save to a particular filename
        or the default "js9.cmap":

        >>> # save the current colormap in js9.cmap
        >>> JS9.SaveColormap()
        >>> # save the current colormap in foo.cmap
        >>> JS9.SaveColormap("foo.cmap")
        >>> # save the foo1 and foo2 colormaps in js9.cmap
        >>> JS9.SaveColormap(["foo1", "foo2"])
        >>> # save the user-defined foo1 and foo2 colormaps in foo.cmap
        >>> JS9.SaveColormap("foo.cmap", ["foo1", "foo2"])

        The colormaps are saved in JSON format. Multiple saved colormaps will
        be stored in a JSON array, while a single saved colormap will be saved
        at the top level.

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

          >>> JS9.AddColormap("i8",
                              [[0,0,0], [0,1,0], [0,0,1], [0,1,1], [1,0,0],
                              [1,1,0], [1,0,1], [1,1,1]]))

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

          >>> JS9.AddColormap("red",
                              [[0,0],[1,1]], [[0,0], [0,0]], [[0,0],[0,0]])
          >>> JS9.AddColormap("blue",
                              [[0,0],[0,0]], [[0,0], [0,0]], [[0,0],[1,1]])
          >>> JS9.AddColormap("purple",
                              [[0,0],[1,1]], [[0,0], [0,0]],[[0,0],[1,1]])

        In the red (blue) colormap, the red (blue) array contains two
        vertices, whose color ranges from no intensity (0) to full
        intensity (1) over the whole range of the colormap (0 to
        1). The same holds true for the purple colormap, except that
        both red and blue change from zero to full intensity.

        For a more complicated example, consider the a colormap, which is
        defined as:

          >>> JS9.AddColormap("a",
                              [[0,0], [0.25,0], [0.5,1], [1,1]],
                              [[0,0], [0.25,1], [0.5,0], [0.77,0], [1,1]],
                              [[0,0], [0.125,0], [0.5, 1], [0.64,0.5],
                               [0.77, 0], [1,0]])

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
        {"name":"i8",
          "colors":[[0,0,0],[0,1,0],[0,0,1],[0,1,1],
                    [1,0,0],[1,1,0],[1,0,1],[1,1,1]]}

        # all 3 vertex arrays for purple colormap in one "vertices" property
        {"name":"purple",
         "vertices":[[[0,0],[1,1]],[[0,0],[0,0]],[[0,0],[1,1]]]}

        Finally, note that JS9.AddColormap() adds its new colormap to
        all JS9 displays on the given page.
        """
        return self.send({'cmd': 'AddColormap', 'args': args})

    def LoadColormap(self, *args):
        """
        Load a colormap file into JS9

        LoadColormap(filename)

        where:

        - filename: input file name or URL

        Load the specified colormap file into the web page. The filename,
        which must be specified, can be a local file (with absolute path or a
        path relative to the displayed web page) or a URL. It should contain a
        JSON representation of a colormap, either in RGB color format or in
        vertex format (see AddColormap() above):

        >>> # RGB color format
        >>> {
        >>>     "name": "purplish",
        >>>     "colors": [
        >>>        [0.196, 0.196, 0.196],
        >>>        [0.475, 0, 0.608],
        >>>        [0, 0, 0.785],
        >>>        [0.373, 0.655, 0.925],
        >>>        [0, 0.596, 0],
        >>>        [0, 0.965, 0],
        >>>        [1, 1, 0],
        >>>        [1, 0.694, 0],
        >>>        [1, 0, 0]
        >>>     ]
        >>> }
        >>> # vertex format
        >>> {
        >>>     "name": "aips0",
        >>>     "vertices": [
        >>>      [
        >>>        [0.203, 0],
        >>>        [0.236, 0.245],
        >>>        [0.282, 0.5],
        >>>        [0.342, 0.706],
        >>>        [0.411, 0.882],
        >>>        [0.497, 1]
        >>>      ],
        >>>      [
        >>>        [0.394, 0],
        >>>        [0.411, 0.196],
        >>>        [0.464, 0.48],
        >>>        [0.526, 0.696],
        >>>        [0.593, 0.882],
        >>>        [0.673, 1],
        >>>        [0.94, 1],
        >>>        [0.94, 0]
        >>>      ],
        >>>      [
        >>>        [0.091, 0],
        >>>        [0.091, 0.373],
        >>>        [0.262, 1],
        >>>        [0.94, 1],
        >>>        [0.94, 0]
        >>>      ] ]
        >>>   }

        As with AddColormap(), the new colormap will be available
        in all displays.
        """
        return self.send({'cmd': 'LoadColormap', 'args': args})

    def GetRGBMode(self, *args):
        """
        Get RGB mode information

        call:

        rgbobj  = JS9.GetRGBMode()

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

        JS9.SetRGBMode(mode, [imobj])

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

    def GetOpacity(self, *args):
        """
        Get the image opacity

        call:

        opacity  = JS9.GetOpacity()

        returns:

        -  opacity: opacity object

        The returned opacity object will contain the following properties:

        - opacity: opacity value assigned to image pixels
        - flooropacity: opacity assigned when the image pixel value is
           less than or equal to the floor value (if defined)
        - floorvalue: floor value to test image pixel values against
           (if defined)
        """
        return self.send({'cmd': 'GetOpacity', 'args': args})

    def SetOpacity(self, *args):
        """
        Set the image opacity

        calling sequences:

        JS9.SetOpacity(opacity)      # set default opacity for all image pixels
        JS9.SetOpacity(fvalue, fopacity) # pixels <= fvalue use fopacity
        JS9.SetOpacity(opacity, fvalue, fopacity)  # set def and floor opacity
        JS9.SetOpacity("reset")      # reset default opacity to 1
        JS9.SetOpacity("resetfloor") # remove opacity floor
        JS9.SetOpacity("resetall")   # reset def opacity to 1, remove floor

        where:

	 - opacity: opacity value for image pixels
	 - floorvalue: floor value to test image pixel values against
	 - flooropacity: floor opacity value to set

        Set the current opacity, floor opacity, or both. This call takes one
        (opacity), two (floorvalue, flooropacity) or three (opacity,
        floorvalue, flooropacity) arguments.

        The floor value & opacity option allows you to set the opacity
        for pixels whose image value is less then or equal to a specified
        floor value. It takes two arguments: the floor pixel value to check,
        and the floor opacity to apply. For example, when both arguments are 0,
        pixels whose image values are less than or equal to 0
        will be transparent. Specifying 5 and 0.5, respectively, means that
        pixels whose image values less than or equal to 5 will have an opacity
        of 0.5. A useful case is to make the pixels transparent at a
        given value, allowing features of one image to be blended into
        another, without blending extraneous pixels.

        The various reset options allow you to reset the default value,
        floor values, or both.
        """
        return self.send({'cmd': 'SetOpacity', 'args': args})

    def GetZoom(self, *args):
        """
        Get the image zoom factor

        call:

        zoom  = JS9.GetZoom()

        returns:

        -  zoom: floating point zoom factor
        """
        return self.send({'cmd': 'GetZoom', 'args': args})

    def SetZoom(self, *args):
        """
        Set the image zoom factor

        call:

        JS9.SetZoom(zoom)

        where:

        -  zoom: floating or integer zoom factor or zoom directive string

        The zoom directives are:

        -  x[n]|X[n]: multiply the zoom by n (e.g. 'x2')
        -  /[n]: divide the zoom by n (e.g. '/2')
        -  in|In: zoom in by a factor of two
        -  out|Out: zoom out by a factor of two
        -  toFit|ToFit: zoom to fit image in display
        """
        return self.send({'cmd': 'SetZoom', 'args': args})

    def GetPan(self, *args):
        """
        Get the image pan position

        call:

        ipos  = JS9.GetPan()

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

        JS9.SetPan(x, y)

        where:

        -  x: x image coordinate
        -  y: y image coordinate

        Set the current pan position using image coordinates. Note that you can
        use JS9.WCSToPix() and JS9.PixToWCS() to convert between image
        and WCS coordinates.
        """
        return self.send({'cmd': 'SetPan', 'args': args})

    def AlignPanZoom(self, *args):
        """
        Align pan and zoom of the current image to a target image

        call:

        JS9.AlignPanZoom(im)

        where:
        - im: image containing the WCS used to perform the alignment

        This routine changes the pan and zoom of the current image to match a
        target image, assuming both have WCS info available. The image is
        panned to the RA, Dec at the center of the target image's display. The
        zoom is also matched. The pixel size (as specified by the FITS CDELT1
        parameter) will be taken into account when zooming, but not the image
        rotation or flip. This routine is faster than ReprojectData() for
        aligning reasonably similar images.

        No attempt is make to keep the images aligned after the call. This
        allows you to make adjustments to the current and/or target images and
        then re-align as needed.
        """
        return self.send({'cmd': 'AlignPanZoom', 'args': args})

    def GetScale(self, *args):
        """
        Get the image scale

        call:

        scale  = JS9.GetScale()

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

        JS9.SetScale(scale, smin, smax)

        where:

        -  scale: scale name
        -  smin: scale min value
        -  smax: scale max value

        Set the current scale, min/max, or both. This call takes one (scale),
        two (smin, max) or three (scale, smin, smax) arguments.
        """
        return self.send({'cmd': 'SetScale', 'args': args})

    def GetFlip(self, *args):
        """
        Get flip state of an image

        call:

        flip  = JS9.GetFlip()

        returns:

        -  flip: current flip state

        Possible returned flip states are: "x", "y", "xy", or "none".
        """
        return self.send({'cmd': 'GetFlip', 'args': args})

    def SetFlip(self, *args):
        """
        Flip an image around the x or y axis

        call:

        JS9.SetFlip(flip)

        where:

        -  flip: "x", "y"

        Flip an image around the specified axis. Flipping is relative to the
        current state of the display, so flipping by x twice will return you
        to the original orientation.

        Since this operation is applied to the entire display canvas instead
        of the image, image parameters such as the WCS are not affected.
        """
        return self.send({'cmd': 'SetFlip', 'args': args})

    def GetRotate(self, *args):
        """
        Get the rotate state of an image

        call:

        flip = JS9.GetRotate()

        returns:

        -  rot:  current rotation value for this image

        Return the current rotation.
        """
        return self.send({'cmd': 'GetRotate', 'args': args})

    def SetRotate(self, *args):
        """
        Rotate an image by a specified number of degrees

        call:

        JS9.SetRotate(rot)

        where:

        -  rot: rotation in degrees

        Set the rotation of an image to the specified number of degrees. The
        rotation is performed in terms of an absolute angle: if you rotate by
        20 degrees and then do it again, there is no change. Also, setting the
        rotation to 0 sets the angle to 0.

        Since this operation is applied to the entire display canvas instead
        of the image, image parameters such as the WCS are not affected.
        """
        return self.send({'cmd': 'SetRotate', 'args': args})

    def GetRot90(self, *args):
        """
        Get the rotate state of an image

        call:

        flip = JS9.GetRot90()

        returns:

        -  rot:  current rotation value for this image

        The returned rotation value will be a multiple of 90, depending on
        how many rotations have been executed and in which direction.
        """
        return self.send({'cmd': 'GetRot90', 'args': args})

    def SetRot90(self, *args):
        """
        Rotate an image by +/- 90 degrees

        call:

        JS9.SetRot90(rot)

        where:

        -  rot: +/- 90

        Rotate an image by a multiple of 90 degrees. Rot90 rotations are
        relative to the current state of the display, so four rotations will
        return you to the original orientation.

        Since this operation is applied to the entire display canvas instead
        of the image, image parameters such as the WCS are not affected.
        """
        return self.send({'cmd': 'SetRot90', 'args': args})

    def GetParam(self, *args):
        """
        Get an image parameter value

        val = GetParam(param)

        where:

        - param: name of the parameter

        returns:

        - val: value of the parameter

        Return the value of an image parameter. The available parameters are
        listed below in the SetParam() section.
        """
        return self.send({'cmd': 'GetParam', 'args': args})

    def SetParam(self, *args):
        """
        Set an image parameter value

        ovalue = SetParam(param, value)

        where:

        - param: name of the parameter
        - val: new value of the parameter

        returns:

        - ovalue: the previous value of the parameter

        A number of miscellaneous image parameters are copied from the
        JS9.imageOpts object to each image when it is first loaded. You can
        use the SetParam() routine to modify these values subsequently.
        The available parameters and their current default values are listed
        below:

        - exp: 1000, default exp value for scaling
        - listonchange: false, list regions after a region change?
        - opacity: 1.0, image display opacity, between 0 and 1
        - nancolor: "#000000", 6-digit #hex color for NaN values
        - valpos: true, display value/position?
        - wcsalign: true, align image using wcs after reproj?
        - xeqonchange: true, xeq an onchange callback after a region change?
        - zscalecontrast: 0.25, default zscale value from ds9
        - zscalesamples: 600, default zscale value from ds9
        - zscaleline: 120, default zscale value from ds9

        The routine returns the previous value of the parameter, which can
        be useful when temporarily turning off a function. For example:

        >>> oval = SetParam("xeqonchange", false);
        >>> .... processing ...
        >>> SetParam("xeqonchange", oval);

        will temporarily disable execution of the previously defined regions
        onload callback, resetting it to the old value after processing
        is complete.
        """
        return self.send({'cmd': 'SetParam', 'args': args})

    def GetValPos(self, *args):
        """
        Get value/position information

        call:

        valpos  = JS9.GetValPos(ipos)

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

        wcsobj  = JS9.PixToWCS(x, y)

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

        pixobj  = JS9.WCSToPix(ra, dec)

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

        dpos  = JS9.ImageToDisplayPos(ipos)

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

        ipos  = JS9.DisplayToImagePos(dpos)

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

        lpos  = JS9.ImageToLogicalPos(ipos, lcs)

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

        ipos  = JS9.LogicalToImagePos(lpos, lcs)

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

        unitsstr  = JS9.GetWCSUnits()

        returns:

        -  unitstr: 'pixels', 'degrees' or 'sexagesimal'
        """
        return self.send({'cmd': 'GetWCSUnits', 'args': args})

    def SetWCSUnits(self, *args):
        """
        Set the current WCS units

        call:

        JS9.SetWCSUnits(unitsstr)

        where:

        -  unitstr: 'pixels', 'degrees' or 'sexagesimal'

        Set the current WCS units.
        """
        return self.send({'cmd': 'SetWCSUnits', 'args': args})

    def GetWCSSys(self, *args):
        """
        Get the current World Coordinate System

        call:

        sysstr  = JS9.GetWCSSys()

        returns:

        -  sysstr: current World Coordinate System ('FK4', 'FK5', 'ICRS',
           'galactic', 'ecliptic', 'image', or 'physical')
        """
        return self.send({'cmd': 'GetWCSSys', 'args': args})

    def SetWCSSys(self, *args):
        """
        Set the current World Coordinate System

        call:

        JS9.SetWCSSys(sysstr)

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

    def DisplayMessage(self, *args):
        """
        Display a text message

        call:

        JS9.DisplayMessage(which, text)

        where:

        - which: "info" or "regions"
        - text: text to display

        The text string is displayed in the "info" area (usually occupied by the
        valpos display) or the "region" area (where regions are displayed). The
        empty string will clear the previous message.
        """
        return self.send({'cmd': 'DisplayMessage', 'args': args})

    def DisplayCoordGrid(self, *args):
        """
        Display a WCS-based coordinate grid

        call:

        JS9.DisplayCoordGrid(mode, opts)

        where:

        - mode: true (display) or false (hide)
        - opts: optional object or json string containing grid parameters

        A coordinate grid displays lines of constant RA and constant Dec, with
        the points of intersection labeled by their RA and Dec values. The
        labels are in sexagesimal notation if the WCS units are sexagesimal,
        otherwise they are in degrees. When using sexagesimal notation, labels
        will be shortened if possible, e.g., if the RA hours are the same in
        two successive labels but the minutes are different, only the minutes
        are shown in the second label.

        If no arguments are supplied, the routine returns true if the
        coordinate grid is currently being displayed, false otherwise. A
        boolean first argument specifies whether to display the coordinate
        grid or not.

        The optional second argument is an opts object (or a json-formatted
        string) containing properties to override the default JS9.Grid.opts
        properties. These properties include:

        - raLines: approx. number of RA grid lines
        - decLines: approx. number of Dec grid lines
        - stride: fineness of grid lines
        - margin: edge margin for displaying a line
        - lineColor: color of grid lines
        - strokeWidth: grid stroke width
        - raAngle: rotation for RA label
        - decAngle: rotation for Dec label
        - labelColor: color of text labels
        - labelFontFamily: label font
        - labelFontSize: label font size
        - labelRAOffx: x offset of RA labels
        - labelRAOffy: y offset of RA labels
        - labelDecOffx: x offset of Dec labels
        - labelDecOffy: y offset of Dec labels
        - degPrec: precision for degree labels
        - sexaPrec: precision for sexagesimal labels
        - reduceDims: reduce lines of smaller image dim?
        - cover: grid lines cover: display or image

        The strokeWidth property determines the width of the grid
        lines. It also serves as a reminder that you can pass other
        standard shape properties in the opts object.

        JS9's label placement algorithm puts labels close to the
        intersection of RA and Dec lines. A number of properties can be
        useful in cases where this simple algorithm is not sufficient:
        the raAngle and decAngle properties allow you to rotate the
        labels with respect to the grid lines. The four
        label[RA,Dec]Off[x,y] properties allow you to move the label with
        respect to the grid lines.  The raSkip and decSkip properties
        allow you to skip labelling the first available lines within the
        display. It can be useful, for example, on a rotated image, when
        the labels are placed in a corner.

        The degPrec and sexaPrec properties specify the precision for
        degree values and segagesimal values, respectively. Higher
        precision will use more digits and take more space along each line.

        A number of properties are (more or less) internal but might be
        of use: the reduceDims property will reduce the raLines and
        decLines properties by the ratio of image dimensions if one
        dimension is smaller than the other. This can prevent crowding in
        the smaller dimension.  The stride property specifies the length
        of each line segment that together make up a grid line. A smaller
        stride might make the grid lines smoother in some cases, at the
        price of more processing time.  The cover property determines
        whether the grid is drawn over the entire image or just the
        displayed part of the image. At the moment, drawing lines over
        the displayed part of the image seems to be sufficient.

        Note that you can specify global site-wide values for all these
        parameters (overriding the JS9.Grid.opts defaults) by supplying them
        in a grid object within the globalOpts object in the js9prefs.js file.

        Example: display a coordinate grid, specifying the line color:

          >>> JS9.DisplayCoordGrid(true, {lineColor: "pink"});
        """
        return self.send({'cmd': 'DisplayCoordGrid', 'args': args})

    def CountsInRegions(self, *args):
        """
        Get background-subtracted counts in regions

        call:

        JS9.CountsInRegions(sregion, bregion, opts)

        where:

        - sregion: source region ("$sregions" for displayed source regions)
        - bregion: background region ("$bregions" for displayed bkgd regions)
        - opts:  optional object or json string containing region parameters

        The regcnts program (and its predecessor, funcnts) counts photons in
        specified source regions and optionally, in specified background
        regions. Displayed results include the bkgd-subtracted counts in each
        region, as well as the error on the counts, the area in each region,
        and the surface brightness (cnts/area**2) calculated for each region.
        Regcnts for desktop use is available on GitHub at:
        https://github.com/ericmandel/regions.

        The regcnts program has been compiled into JS9 using Emscripten.
        Using this routine, regcnts can be run on the FITS memory-based file
        for the currently displayed image.  The first two arguments specify
        the source region(s) and background region(s), respectively.
        You can pass a standard region specifier as the source
        or background region. If the string "$sregions" ("$bregions") is
        specified, the source (background) regions are taken from the
        currently displayed image.

        In keeping with how desktop regcnts works, if no argument or null or a
        null string is specified as the source region, the entire field is
        used as the source region. If no argument or null or a null string is
        explicitly specified as a background region, no regions are used for
        the background.  In particular, if you pass only the source region
        argument, or pass only the source region and opts arguments, no
        background region is used. To recap:

          >>> # use entire field, no background
          >>> JS9.CountsInRegions([opts])
          >>> JS9.CountsInRegions("field"||null||""[, opts])

          >>> # use displayed source and displayed background
          >>> JS9.CountsInRegions("$sregions", "$bregions"[, opts])

          >>> # use displayed source, no background
          >>> JS9.CountsInRegions("$sregions"[, opts])

          >>> # use displayed source and specified background
          >>> JS9.CountsInRegions("$sregions", bregions[, opts])

          >>> # use specified source, no background
          >>> JS9.CountsInRegions(sregions[, opts])

          >>> # use specified source and specified background
          >>> JS9.CountsInRegions(sregions, bregions[, opts])

          >>> # use specified source and displayed background
          >>> JS9.CountsInRegions(sregions, "$bregions"[, opts])

          >>> # use entire field and specified background
          >>> JS9.CountsInRegions("field"||null||"", bregions[, opts])

          >>> # use entire field and displayed background
          >>> JS9.CountsInRegions("field"||null||"", "$bregions"[, opts])

        The third argument allows you to specify options to regcnts:

        - cmdswitches: command line switches passed to regcnts
        - dim: size of reduced image (def: max of JS9.globalOpts.image.[xy]dim)
        - reduce: reduce image size? (def: true)
        - lightwin: if true, results displayed in light window

        The command line switches that can be specified in cmdswitches are
        detailed in https://js9.si.edu/regions/regcnts.html, the regcnts help
        page.  Aside from switches which control important aspects of the
        analysis, the "-j" switch (which returns the output in JSON format)
        might be useful in the browser environment. Some examples:

          >>> # display results in a light window
          >>> JS9.CountsInRegions({lightwin: true})

          >>> # return json using maximum precision in output
          >>> JS9.CountsInRegions({cmdswitches: "-j -G"})

        Results are also returned as a text string.

        The regcnts code is memory (and cpu) intensive. In the desktop
        environment, this is not typically a problem, but the
        memory-constrained browser environment can present a challenge for
        large images and binary tables.  To avoid running out of memory (and
        for large images, to speed up processing considerably), the
        CountsInRegions() routine will bin the image to reduce its size,
        unless the reduce option is explicitly set to false. The binned
        image size can be specified by the dim option, defaulting to
        the global value of the image dimension options. When a file is binned
        in this manner, the returned resolution value (e.g., arcsec/pixel)
        will reflect the applied binning. Note that the number of photons
        found inside a binned and unbinned region differ slightly, due to the
        difference in the pixel boundaries in the two cases.

        The Counts in Regions option of the Analysis -> Client-side
        Analysis menu runs regcnts on the source and background regions of
        the currently displayed image. The results are displayed in a light
        window.

        Finally, note that the main JS9 web site at https://js9.si.edu
        also offers regcnts as a server-based analysis program in the
        Analysis menu. The displayed source and background regions are passed
        to the server for processing. Because this version runs the desktop
        program, it runs on the original file and does no binning to reduce
        the image size (which, by the way, could lengthen the processing
        time). But the server-side task also can be useful for
        JS9 large file support, which involves displaying a small
        representation file associated with a much larger parent
        file stored on the server. In this case, you often want to run
        the analysis on the larger (original) file.
        """
        return self.send({'cmd': 'CountsInRegions', 'args': args})

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

        - op: image operation: "add", "sub", "mul", "div",
                               "min", "max", "reset"
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

        - brighten(val): add const val to each pixel to change the brightness:
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

        The opts object can contain the following reproject-specific props:

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

    def RotateData(self, *args):
        """
        Rotate an image around the WCS CRPIX point

        call:

        JS9.RotateData(angle, opts)

        where:

        - angle: rotation angle in degrees
        - opts: options object

        The JS9.RotateData() routine uses JS9.ReprojectData() to rotate
        image data by the specified angle (in degrees). If the string
        "northup" or "northisup" is specified, the rotation angle is set to 0.
        The rotation is performed about the WCS CRPIX1, CRPIX2 point.

        The optional opts object is passed directly to the JS9.ReprojectData()
        routine. See JS9.ReprojectData() above for more information.
        """
        return self.send({'cmd': 'RotateData', 'args': args})

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

        lid  = JS9.NewShapeLayer(layer, opts)

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

        JS9.ShowShapeLayer(layer, mode)

        where:

        -  layer: name of layer
        -  mode: true (show layer) or false (hide layer)

        Shape layers can be hidden from display. This could be useful, for
        example, if you have several catalogs loaded into a display and want to
        view one at a time.

        If mode is true, a previously hidden shape layer will be displayed. If
        mode is false, a displayed shape layer will be hidden. If the
        mode argument is not supplied, the current mode is returned.
        """
        return self.send({'cmd': 'ShowShapeLayer', 'args': args})

    def ToggleShapeLayers(self, *args):
        """
        Toggle display of the active shape layers

        call:

        JS9.ToggleShapeLayers()

        While ShowShapeLayer() allows you to display or hide a single shape
        layer, this routine will toggle display of all active layers in the
        current image. An active layer is one that has not been turned off
        usng the Shape Layers plugin or ShowShapeLayer().

        The routine remembers which layers were active at the moment when
        layers are hidden and restores only those layers in the next toggle.
        Thus, if you have two layers, "regions" and "catalog1", and the
        "catalog1" layer has previously been turned off, calling this routine
        repeatedly will turn on and off the "regions" layer only.

        """
        return self.send({'cmd': 'ToggleShapeLayers', 'args': args})

    def ActiveShapeLayer(self, *args):
        """
        Make the specified shape layer the active layer

        call:

        JS9.ActiveShapeLayer(layer)

        where:

        - layer: name of layer

        returns:

        -  active: the active shape layer (if no args are specified)

        For a given image, one shape layer at a time is active, responding to
        mouse and touch events. Ordinarily, a shape layer becomes the active
        layer when it is first created and shapes are added to it. Thus, the
        first time you create a region, the regions layer becomes active. If
        you then load a catalog into a layer, that layer becomes active.

        If no arguments are supplied, the routine returns the currently active
        layer. Specify the name of a layer as the first argument to make it
        active. Note that the specified layer must be visible.
        """
        return self.send({'cmd': 'ActiveShapeLayer', 'args': args})

    def AddShapes(self, *args):
        """
        Add one or more shapes to the specified layer

        call:

        JS9.AddShapes(layer, sarr, opts)

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

        JS9.RemoveShapes(layer, shapes)

        where:

        -  layer: name of layer
        -  shapes: which shapes to remove

        If the shapes argument is not specified, it defaults to "all". You
        can specify a selector using any of the following:

        -  all: all shapes not including child text shapes
        -  All: all shapes including child text shapes
        -  selected: the selected shape (or shapes in a selected group)
        -  [color]: shapes of the specified color
        -  [shape]: shapes of the specified shape
        -  [wcs]:  shapes whose initial wcs matches the specified wcs
        -  [tag]:  shapes having the specified tag
        -  /[regexp]/: shapes with a tag matching the specified regexp
        -  child: a child shape (i.e. text child of another shape)
        -  parent: a shape that has a child (i.e. has a text child)
        """
        return self.send({'cmd': 'RemoveShapes', 'args': args})

    def GetShapes(self, *args):
        """
        Get information about one or more shapes in the specified shape
        layer

        call:

        JS9.GetShapes(layer, shapes)

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

        JS9.ChangeShapes(layer, shapes, opts)

        where:

        -  layer: name of layer
        -  shapes: which shapes to change
        -  opts: object containing options to change in each shape

        Change one or more shapes. The opts object can contain the parameters
        described in the JS9.AddShapes() section. However, you cannot (yet)
        change the shape itself (e.g. from 'box' to 'circle').

        If the shapes argument is not specified, it defaults to "all". You
        can specify a selector using any of the following:

        -  all: all shapes not including child text shapes
        -  All: all shapes including child text shapes
        -  selected: the selected shape (or shapes in a selected group)
        -  [color]: shapes of the specified color
        -  [shape]: shapes of the specified shape
        -  [wcs]:  shapes whose initial wcs matches the specified wcs
        -  [tag]:  shapes having the specified tag
        -  /[regexp]/: shapes with a tag matching the specified regexp
        -  child: a child shape (i.e. text child of another shape)
        -  parent: a shape that has a child (i.e. has a text child)
        """
        return self.send({'cmd': 'ChangeShapes', 'args': args})

    def CopyShapes(self, *args):
        """
        Copy a shape layer to another image

        call:

        JS9.CopyShapes(to, layer)

        where:

        -  to: image id to which to copy shapes
        -  layer: shape layer to copy

        Copy regions to a different image. If to is "all", then the
        regions are copied to all images.

        All shapes in the shape layer are copied to the new image.
        """
        return self.send({'cmd': 'CopyShapes', 'args': args})

    def SelectShapes(self, *args):
        """
        Gather Shapes into a Selection

        call:

        JS9.SelectShapes(layer, shapes)

        where:

        -  layer: shape layer
        -  shapes: which shapes to select

        JS9 has a rich mouse-based interface for selecting shapes: a single
        shape is selected by clicking on it. A number of shapes can be
        gathered into a group selection by pressing the left mouse button and
        dragging the mouse over the desired shapes. To add to an
        already-existing selection, shift-click the mouse on a shape.

        This routine allows you to create a selection programmatically by
        specifying which shapes make up the selection. The first argument
        is the shape layer. The second argument is the regions selection.
        If not specified, it defaults to "all". The call creates a selection
        of shapes which can be moved as one unit.

        For example:

        >>> j.SelectShapes("myreg", "circle") # select all circles
        >>> j.SelectShapes("myreg", "circle&&!foo2") # circles w/o 'foo2' tag

        Regions in a selection are processed individually, i.e. a regions
        selection will match the regions inside a group. Thus for example,
        if you create a selection containing circles, changing the color using
        the "circle" specification will also affect the circles within the
        selection. You can, of course, process only the regions inside a
        selection using the selected specification.
        """
        return self.send({'cmd': 'SelectShapes', 'args': args})

    def UnselectShapes(self, *args):
        """Remove Shapes From a Selection

        call:

        JS9.UnselectShapes(layer, shapes)

        where:

        -  layer: shape layer
        -  shapes: which shapes to select

        JS9 has a rich mouse-based interface for selecting shapes: a single
        shape is selected by clicking on it. A number of shapes can be
        gathered into a group selection by pressing the left mouse button and
        dragging the mouse over the desired shapes. To add to an
        already-existing selection, shift-click the mouse on a shape.

        This routine allows you to remove one or more shapes from a shape
        selection programmatically by specifying which shapes to remove.
        The first argument is the shape layer. The second argument is the
        shape selection. In not specified, or specified as "all" or "selected",
        the selection is undone.  Otherwise the call will make a new
        selection, not containing the unselected shapes, which can be
        moved as one unit.
        """
        return self.send({'cmd': 'UnselectShapes', 'args': args})

    def GroupShapes(self, *args):
        """
        Gather Shapes into a Long-lived Group

        call:

        JS9.GroupShapes(layer, shapes, opts)

        where:

        -  layer: shape layer
        -  shapes: which shapes to group
        -  opts: optional object containing grouping options

        returns:

        -  groupid: the group id associated with the newly created group

        A shape group can be moved and resized as a single unit. To
        first order, it is a long-lived form of a region selection.
        The latter gets dissolved when you click the mouse outside the
        selection, but a shape group is dissolved only by
        calling j.UngroupShapes().

        This routine allows you to create a group by specifying the shapes
        which will compose it.  The first argument is the regions selection.
        If not specified, it defaults to either 'selected' or 'all', depending
        on whether a shape selection currently exits.

        The optional opts argument contains the following properties:

        -  groupid: the group id to use, if possible (default: 'group_[n]')
        -  select: if false, the group is not selected upon creation

        By default, the groupid will be the string 'group_' followed by
        an integer chosen so that the groupid is unique. You can supply your
        own groupid, but if it already is associated with an existing group,
        an integer value will be appended to make it unique. Also, by default
        the newly created group will be 'selected'.  You can pass
        the select property with a value of false in order to
        avoid selecting the group (e.g., if you are creating a number of
        groups and do not want to see each of them selected in turn.)

        The returned groupid string can be used to select and process all the
        shapes in that group. Thus, for example, you can use the groupid to
        change the color of all grouped shapes:

        >>> gid = j.GroupShapes('myreg', 'circle && foo1');
        >>> j.ChangeShapes('myreg', gid, {'color':'red'});

        Note however, that unlike the temporary shape selections, shapes
        in a group are not available individually, i.e., a regions selection
        using a non-groupid does not match shapes inside a group. Thus, for
        example, if you have created a group of circles, changing the color
        using a 'circle' specification does not affect circles within the group:

        >>> gid = j.GroupShapes('myreg', 'circle && foo1');
        >>> j.ChangeShapes('myreg', 'circle', {'color':'cyan'}) # no
        >>> j.ChangeShapes('myreg', gid, {'color':'red'});      # yes

        Furthermore, a given shape can only be part of one group at a
        time. In the case where a shape already is part of an existing group,
        the globalOpts.regGroupConflict property determines how that shape
        is processed.  The default is skip, meaning that the shape is
        silently skipped over when creating the new group. The alternative
        is error, which will throw an error.
        """
        return self.send({'cmd': 'GroupShapes', 'args': args})

    def UngroupShapes(self, *args):
        """
        Dissolve a Group of Shapes

        call:

        JS9.UngroupShapes(layer, groupid, opts)

        where:

        -  layer: shape layer
        -  groupid: group id of the group to dissolve
        -  opts: optional object containing ungrouping options

        This routine allows you to dissolve an existing group, so that the
        shapes contained therein once again become separate. The first
        argument is the groupid, previously returned by the JS9.GroupShapes()
        call.

        The optional opts argument contains the following properties:

        -  select: newly separate shapes in the group are 'selected'?

        By default, the ungrouped shapes unobtrusively take their place among
        other shapes on the display. You can make them be selected by
        passing the select: true property in opts. Doing this, for
        example, would allow you to remove them easily with the Delete key.

        For example:
        >>> gid = j.GroupShapes('myreg', 'circle || ellipse')
        >>> j.UngroupShapes('myreg', gid)
        """
        return self.send({'cmd': 'UngroupShapes', 'args': args})

    def AddRegions(self, *args):
        """
        Add one or more regions to the regions layer

        call:

        id  = JS9.AddRegions(rarr, opts)

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

        rarr  = JS9.GetRegions(regions)

        where:

        -  regions: which regions to retrieve

        returns:

        -  rarr: array of region objects

        If the regions argument is not specified, it defaults to
        "selected" if there are selected regions, otherwise "all".
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

    def ListRegions(self, *args):
        """
        List one or more regions

        call:

        JS9.ListRegions(regions, opts)

        where:

        -  regions: which regions to list
        -  opts: object containing options

        List (and return) the specified regions. By default, a light window
        is displayed listing all regions (i.e., as if the list option of the
        Regions menu had been selected.) You can also list "selected" regions
        or use any of the standard regions specifications.

        The opts object supports the following properties:
        -  mode: display/return mode (1,2,3)
        -  wcssys: wcs system to use (ICRS, FK5, galactic, physical, etc.)
        -  wcsunits: units for wcs output (sexagesimal, degrees, pixels)
        -  includejson: include JSON object
        -  includecomments: include comments
        -  layer: which layer to display (def: regions layer)

        The mode property accepts the following values:
        -  1: no display, return full region string including json, comments
        -  2: display and return shortened region string (no json, comments)
        -  3: display and return full region string (including json, comments)
        """
        return self.send({'cmd': 'ListRegions', 'args': args})

    def ListGroups(self, *args):
        """
        List one or more region/shape groups

        call:

        JS9.ListGroups(group, opts)

        where:

        -  group: which group(s) to list
        -  opts: object containing options

        List the specified region/shape group(s) in the specified layer
        (default is "regions").  The first argument is the groupid of the
        group to list, or "all" to list all groups.

        The optional opts object can contain the following properties:

        -  includeregions: display regions as well as the group name (def: true)
        -  layer: layer to list (def: "regions")

        By default, the display will includes the name of the group and the
        regions in the group. To skip the display of regions, supply
        an opts object with the includeregions property set to False.

        For example:

        >>> j.ListGroups("all", {"includeregions": false})
        grp1
        grp2
        grp3

        >>> j.ListGroups("grp1")
        grp1:
        circle(3980.00,4120.00,20.00) # source,include,foo1
        ellipse(4090.00,4120.00,25.00,15.00,0.0000) # source,include,foo1
        """
        return self.send({'cmd': 'ListGroups', 'args': args})

    def EditRegions(self, *args):
        """
        Edit one or more regions

        call:

        JS9.EditRegions()

        Edit one or more selected regions using an Edit dialog box. If a
        single region has been selected by clicking that region, all of its
        properties can be edited via the displayed dialog box. If a group of
        regions has been selected using Meta-mousemove to highlight one or
        more regions, then properties such as color, stroke width, dash
        pattern, and tags can be edited for all of the selected regions using
        the displayed dialog box. In the latter case, use shift-click to add
        additional regions to the edit group.
        """
        return self.send({'cmd': 'EditRegions', 'args': args})

    def ChangeRegions(self, *args):
        """
        Change one or more regions

        call:

        JS9.ChangeRegions(regions, opts)

        where:

        -  regions: which regions to change
        -  opts: object containing options to change in each region

        Change one or more regions. The opts object can contain the parameters
        described in the JS9.AddRegions() section. However, you cannot (yet)
        change the shape itself (e.g. from 'box' to 'circle'). See
        js9onchange.html for examples of how to use this routine.

        If the regions argument is not specified, it defaults to
        "selected" if there are selected regions, otherwise "all".
        You can specify a region selector using any of the following:

        -  all: all regions not including child text regions
        -  All: all regions including child text regions
        -  selected: the selected region (or regions in a selected group)
        -  [color]: regions of the specified color
        -  [shape]: regions of the specified shape
        -  [wcs]:  regions whose initial wcs matches the specified wcs
        -  [tag]:  regions having the specified tag
        -  /[regexp]/: regions with a tag matching the specified regexp
        -  child: a child region (i.e. text child of another region)
        -  parent: a region that has a child (i.e. has a text child)
        """
        return self.send({'cmd': 'ChangeRegions', 'args': args})

    def CopyRegions(self, *args):
        """
        Copy one or more regions to another image

        call:

        JS9.CopyRegions(to, regions)

        where:

        -  to: image id to which to copy regions
        -  regions: which regions to copy

        Copy regions to a different image. If to is "all", then the
        regions are copied to all images.

        If the regions argument is not specified, it defaults to
        "selected" if there are selected regions, otherwise "all".
        You can specify a region selector using any of the following:

        -  all: all regions not including child text regions
        -  All: all regions including child text regions
        -  selected: the selected region (or regions in a selected group)
        -  [color]: regions of the specified color
        -  [shape]: regions of the specified shape
        -  [wcs]:  regions whose initial wcs matches the specified wcs
        -  [tag]:  regions having the specified tag
        -  /[regexp]/: regions with a tag matching the specified regexp
        -  child: a child region (i.e. text child of another region)
        -  parent: a region that has a child (i.e. has a text child)
        """
        return self.send({'cmd': 'CopyRegions', 'args': args})

    def RemoveRegions(self, *args):
        """
        Remove one or more regions from the region layer

        call:

        JS9.RemoveRegions(regions)

        where:

        -  regions: which regions to remove

        If the regions argument is not specified, it defaults to
        "selected" if there are selected regions, otherwise "all".
        You can specify a region selector using any of the following:

        -  all: all regions not including child text regions
        -  All: all regions including child text regions
        -  selected: the selected region (or regions in a selected group)
        -  [color]: regions of the specified color
        -  [shape]: regions of the specified shape
        -  [wcs]:  regions whose initial wcs matches the specified wcs
        -  [tag]:  regions having the specified tag
        -  /[regexp]/: regions with a tag matching the specified regexp
        -  child: a child region (i.e. text child of another region)
        -  parent: a region that has a child (i.e. has a text child)
        """
        return self.send({'cmd': 'RemoveRegions', 'args': args})

    def UnremoveRegions(self, *args):
        """
        Unremove one or more previously removed regions

        call:

        JS9.RemoveRegions()

        If you accidentally remove one or more regions, you can use restore
        them using this call. JS9 maintains a stack of removed regions (of
        size JS9.globalOpts.unremoveReg, current default is 100). Each
        time one or more regions is removed, they are stored as a single entry
        on this stack. The UnremoveRegions call pops the last entry off
        the stack and calls AddRegions.
        """
        return self.send({'cmd': 'UnremoveRegions', 'args': args})

    def SaveRegions(self, *args):
        """
        Save regions from the current image to a file

        call:

        JS9.SaveRegions(filename, which, layer)

        where:

        - filename: output file name
        - which: which regions to save (def: "all")
        - layer: which layer save (def: "regions")

        Save the current regions for the displayed image as JS9 regions file.
        If filename is not specified, the file will be saved as "js9.reg".

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

    def SelectRegions(self, *args):
        """
        Group Regions into a Selection

        call:

        JS9.SelectRegions(regions)

        where:

        -  regions: which regions to select

        JS9 has a rich mouse-based interface for selecting regions: a single
        region is selected by clicking on it. A number of regions can be
        gathered into a group selection by pressing the left mouse button and
        dragging the mouse over the desired regions. To add to an
        already-existing selection, shift-click the mouse on a region.

        This routine allows you to create a selection programmatically by
        specifying which regions make up the selection.  The first argument is
        the regions selection.  If not specified, it defaults to "all".
        The call makes a selection of regions which can be moved as one unit.

        For example:

        >>> j.SelectRegions("circle") # select all circles
        >>> j.SelectRegions("circle && !foo2") # all circles without tag 'foo2'

        Regions in a selection are processed individually, i.e. a regions
        selection will match the regions inside a group. Thus for example,
        if you create a selection containing circles, changing the color using
        the "circle" specification will also affect the circles within the
        selection. You can, of course, process only the regions inside a
        selection using the selected specification.
        """
        return self.send({'cmd': 'SelectRegions', 'args': args})

    def UnselectRegions(self, *args):
        """
        Remove Regions From a Selection

        call:

        JS9.UnselectRegions(regions)

        where:

        -  regions: which regions to select

        JS9 has a rich mouse-based interface for selecting regions: a single
        region is selected by clicking on it. A number of regions can be
        gathered into a group selection by pressing the left mouse button and
        dragging the mouse over the desired regions. To add to an
        already-existing selection, shift-click the mouse on a region.

        This routine allows you to remove one or more regions from a region
        selection programmatically by specifying which regions to remove.
        The first argument is the regions selection. In not specified,
        or specified as "all" or "selected", the selection is undone.
        Otherwise the call will make a new selection, not containing
        the unselected regions, which can be moved as one unit.

        For example:

        >>> j.UnselectRegions("circle&&!foo2") # unselect circles w/o tag 'foo2'
        """
        return self.send({'cmd': 'UnselectRegions', 'args': args})

    def GroupRegions(self, *args):
        """
        Gather Regions into a Long-lived Group

        call:

        JS9.GroupRegions(shapes, opts)

        where:

        -  regions: which regions to group
        -  opts: optional object containing grouping options

        returns:

        -  groupid: the group id associated with the newly created group

        A region group can be moved and resized as a single unit. To
        first order, it is a long-lived form of a region selection.
        The latter gets dissolved when you click the mouse outside the
        selection, but a region group is dissolved only by calling
        JS9.UngroupRegions().

        This routine allows you to create a group by specifying the regions
        which will compose it.  The first argument is the regions selection.
        If not specified, it defaults to either 'selected' or 'all', depending
        on whether a region selection currently exits.

        The optional opts argument contains the following properties:

        -  groupid: the group id to use, if possible (default: 'group_[n]')
        -  select: if false, the group is not selected upon creation

        By default, the groupid will be the string 'group_' followed by
        an integer chosen so that the groupid is unique. You can supply your
        own groupid, but if it already is associated with an existing group,
        an integer value will be appended to make it unique. Also, by default
        the newly created group will be 'selected'.  You can pass
        the select property with a value of false in order to
        avoid selecting the group (e.g., if you are creating a number of
        groups and do not want to see each of them selected in turn.)

        The returned groupid string can be used to select and process all the
        regions in that group. Thus, for example, you can use the groupid to
        change the color of all grouped regions:

        >>> gid = j.GroupRegions('circle && foo1');
        >>> j.ChangeRegions(gid, {'color':'red'});

        Furthermore, when creating a regions file via JS9.SaveRegions(),
        the groupid will be stored in each grouped region's JSON object, and
        will be used to reconstitute the group when the file is reloaded.

        Note however, that unlike the temporary region selections, regions
        in a group are not available individually, i.e., a regions selection
        using a non-groupid does not match regions inside a group. Thus, for
        example, if you have created a group of circles, changing the color
        using a 'circle' specification does not affect circles within the group:

        >>> gid = j.GroupRegions('circle && foo1');
        >>> j.ChangeRegions('circle', {'color':'cyan'}) # won't change group
        >>> j.ChangeRegions(gid, {'color':'red'}); # change regions in group

        Furthermore, a given region can only be part of one group at a
        time. In the case where a region already is part of an existing group,
        the globalOpts.regGroupConflict property determines how that region
        is processed.  The default is skip, meaning that the region is
        silently skipped over when creating the new group. The alternative
        is error, which will throw an error.
        """
        return self.send({'cmd': 'GroupRegions', 'args': args})

    def UngroupRegions(self, *args):
        """
        Dissolve a Group of Regions

        call:

        JS9.UngroupRegions(groupid, opts)

        where:

        -  groupid: group id of the group to dissolve
        -  opts: optional object containing ungrouping options

        This routine allows you to dissolve an existing group, so that the
        regions contained therein once again become separate. The first
        argument is the groupid, previously returned by the JS9.GroupRegions()
        call.

        The optional opts argument contains the following properties:

        -  select: newly separate regions in the group are 'selected'?

        By default, the ungrouped regions unobtrusively take their place among
        other regions on the display. You can make them be selected by
        passing the select: true property in opts. Doing this, for
        example, would allow you to remove them easily with the Delete key.

        For example:

        >>> gid = j.GroupRegions('circle || ellipse')
        >>> j.UngroupRegions(gid)
        """
        return self.send({'cmd': 'UngroupRegions', 'args': args})

    def ChangeRegionTags(self, *args):
        """
	Change region tags for the specified image(s)

        call:

        JS9.ChangeRegionTags(which, addreg, removereg)

        where:

        - which: which regions to process (def: 'all')
	- addreg: array or comma-delimited string of regions to add
	- removereg: array or comma-delimited string of regions to remove

	While region tags can be changed wholesale using JS9.ChangeRegions(),
	this routine allows you to add and/or remove specific tags. The first
	argument specifies which regions to change. The second argument is a
	list of tags to add, while the third argument is a list of tags to
	remove. In each case, the tags argument can be an array of tag strings
	or a single string containing a comma-separated list of tags:

	>>> JS9.ChangeRegionTags('selected', ['foo1', 'foo2'], ['goo1']);
	>>> JS9.ChangeRegionTags('selected', 'foo1,foo2', 'goo1');

        """
        return self.send({'cmd': 'ChangeRegionTags', 'args': args})

    def ToggleRegionTags(self, *args):
        """
	Toggle two region tags for the specified image(s)

        call:

        JS9.toggleRegionTags(which, tag1, tag2)

        where:

        - which: which regions to process (def: 'all')
	- tag1: tag #1 to toggle
	- tag2: tag #2 to toggle

	While region tags can be changed wholesale using JS9.ChangeRegions(),
	this routine allows you to toggle between two tags, e.g., a source
	region and background region, or include and exclude. For example:

	>>> JS9.ToggleRegionTags('selected', 'source', 'background');

	will change a background region into a source region
	or vice-versa, depending on the state of the region, while:

	>>> JS9.ToggleRegionTags('selected', 'include', 'exclude');

	will toggle between include and exclude.

        """
        return self.send({'cmd': 'ToggleRegionTags', 'args': args})

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

            Astronomical catalogs are a special type of shape layer, in which
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

            The third argument is an optional object used to specify
            parameters, including:

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
            layer to save must be a catalog created from a tab-delimited
            file (or URL) of catalog objects (e.g., not the regions layer).
        """
        return self.send({'cmd': 'SaveCatalog', 'args': args})

    def GetAnalysis(self, *args):
        """
        Get server-side analysis task definitions

        call:

        JS9.GetAnalysis()

        The JS9.GetAnalysis() routine returns an array of analysis task
        definitions, each containing the following information:

        - name: a short identifier string (typically one word)
        - title: a longer string that will be displayed in the Analysis menu
        - files: a rule that will be matched against to determine whether this
        - task is available for the current image
        - purl: a URL pointing to a web page containing a user parameter form
        - action: the command to execute on the server side
        - rtype: return type: text, plot, fits, png, regions, catalog, alert,
        none
        - hidden: if true, the analysis task is not shown in the Analysis menu

        Not every property will be present in every task definition
        (e.g., purl is only present when there is a parameter form).
        Also note that hidden tasks are not returned by this call.
        """
        return self.send({'cmd': 'GetAnalysis', 'args': args})

    def RunAnalysis(self, *args):
        """
        Run a simple server-side analysis task

        call:

        JS9.RunAnalysis(name, parr)

        where:

        -  name: name of analysis tool
        -  parr: optional array of macro-expansion options for command line

        The JS9.RunAnalysis() routine is used to execute a server-side analysis
        task and return the results for further processing within Python.

        NB: Prior to JS9 v1.10, this routine displayed the results on the JS9
        web page instead of returning them to Python. If you want to display
        the results in JS9, use the "analysis" short-cut routine instead.

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

        JS9.SavePNG(filename, opts)

        where:

        - filename: output file name
        - opts: optional save parameters

        Save the currently displayed image as a PNG file. If filename is not
        specified, the file will be saved as "js9.png".

        The opts object can specify the following properties:

        - layers: save graphical layers (e.g. regions) (def: true)
        - source: "image" or "display" (def: "display")

        By default, SavePNG() will save all of the 2D graphics in the
        shape layers (regions, catalogs, etc.) as well as the image. Set
        the layers property to false to save only the image.

        Also by default, SavePNG() will save the RGB pixels from the
        display. This means, for example, that a blended set of images will
        save the blended pixels. If you want to save the RGB pixels from one
        of the images in a blended image, you can specify the source
        property to the image. For example, in the js9blend.html demo,
        you can save the RGB pixels of the Chandra image by specifying use of
        the "image" source and specifying the image's id in the display
        parameter:

        >>> SavePNG("foo.png", {"source":"image"}, {"display":"chandra.fits"});

        Don't forget that the file is saved by the browser, in whatever
        location you have set up for downloads.
        """
        return self.send({'cmd': 'SavePNG', 'args': args})

    def SaveJPEG(self, *args):
        """
        Save image as a JPEG file

        call:

        JS9.SaveJPEG(filename, opts)

        where:

        - filename: output file name
        - opts: optional save parameters or a number between 0 and 1
                indicating image quality

        Save the currently displayed image as a JPEG file. If filename is not
        specified, the file will be saved as "js9.png".

        The opts object can specify the following properties:
        - layers: save graphical layers (e.g. regions) (def: true)
        - source: "image" or "display" (def: "display")
        - quality: JPEG encoder quality

        By default, SaveJPEG() will save all of the 2D graphics in the
        shape layers (regions, catalogs, etc.) as well as the image. Set
        the layers property to false to save only the image.

        Also by default, SaveJPEG() will save the RGB pixels from the
        display. This means, for example, that a blended set of images will
        save the blended pixels. If you want to save the RGB pixels from one
        of the images in a blended image, you can specify the source
        property to the image. For example, in the js9blend.html demo,
        you can save the RGB pixels of the Chandra image by specifying use of
        the "image" source and specifying the image's id in the display
        parameter:

        >>> SaveJPEG("foo.png", {"source":"image"}, {"display":"chandra.fits"});

        If encoder quality parameter is not specified, a suitable default is
        used. On FireFox (at least), this default values is 0.95 (I think).

        Don't forget that the file is saved by the browser, in whatever
        location you have set up for downloads.
        """
        return self.send({'cmd': 'SaveJPEG', 'args': args})

    def GetToolbar(self, *args):
        """
        Get toolbar values from the Toolbar plugin

        val = GetToolbar(type)

        where:

        - type: type of information to retrieve

        returns:

        - val: array of tool objects (or an argument-dependent return)

        The GetToolbar() routine returns global information about the
        Toolbar plugin. If the first argument is "showTooltips", the returned
        value specifies whether tooltips are currently displayed. Otherwise
        an array of tool objects is returned, one for each of the defined
        tools in the toolbar.
        """
        return self.send({'cmd': 'GetToolbar', 'args': args})

    def SetToolbar(self, *args):
        """
        Set toolbar values for the Toolbar plugin

        SetToolbar(arg1, arg2)

        where:

        - arg1: a type-dependent id or value to set
        - arg2: a type-dependent value to set

        The SetToolbar() routine sets global information about the Toolbar
        plugin. The following values can be specified as the first argument:

        - init: the text "init" triggers a re-initialization of all
        display Toolbar plugins, which is useful if you have changed
        the JS9.globalOpts.toolBar array to specify a new set of
        top-level tools.
        - showTooltips: the text "showTooltips" uses the value of the
        boolean arg2 to specify whether tooltips are displayed as the mouse
        hovers over a tool.
        - [text]: other text is assumed to be a JSON-formatted text
        containing either a new tool to add to the toolbar, or an array of
        tools.
        - [object]: an object is assumed to be new tool to add to the toolbar
        - [array]: an array is assumed to be an array of new tools to add to
        the toolbar

        New tools can be added to the toolbar at any time using this routine.
        The text properties associated with a tool object are:

        - name: name of the tool
        - tip: a tooltip to display when the mouse hovers over the tool
        - image: url (relative to the install directory) containing a PNG
        image file to display as the tool icon
        - cmd: name of the JS9 public routine to execute when the tool is
        clicked
        - args: array of arguments to pass to the JS9 public routine

        Only the name and cmd properties are required. If no image is
        specified, a button labeled by the name value will be used.

        Examples of tool objects:

        >>> {
        >>>   "name": "linear",
        >>>   "tip": "linear scale",
        >>>   "image": "images/toolbar/dax_images/lin.png",
        >>>   "cmd": "SetScale",
        >>>   "args": ["linear"]
        >>> },
        >>> {
        >>>   "name": "histeq",
        >>>   "tip": "histogram equalization",
        >>>   "cmd": "SetScale",
        >>>   "args": ["histeq"]
        >>> },
        >>> {
        >>>   "name": "annulus",
        >>>   "tip": "annulus region",
        >>>   "image": "images/toolbar/dax_images/annulus.png",
        >>>   "cmd": "AddRegions",
        >>>   "args": ["annulus"]
        >>> },
        >>> {
        >>>   "name": "remove",
        >>>   "tip": "remove selected region",
        >>>   "image": "images/toolbar/dax_images/erase.png",
        >>>   "cmd": "RemoveRegions",
        >>>   "args": ["selected"]
        >>> },
        >>> {
        >>>   "name": "zoom1",
        >>>   "tip": "zoom 1",
        >>>   "image": "images/toolbar/dax_images/mag_one.png",
        >>>   "cmd": "SetZoom",
        >>>   "args": [1]
        >>> },
        >>> {
        >>>   "name": "magnifier",
        >>>   "tip": "toggle magnifier display",
        >>>   "image": "images/toolbar/dax_images/mag.png",
        >>>   "cmd": "DisplayPlugin",
        >>>   "args": ["JS9Magnifier"]
        >>> }

        Each time a tool is added to the list of available tools, the active
        Toolbar plugins will be re-initialized to display that tool. By
        default, the new tool not be added to the top-level list: you must
        also edit the JS9.globalOpts.toolBar array to add the name of the
        tool. If this is done after you add the tool, remember to re-initialize
        active toolbars by calling:

        >>>  SetToolbar("init");
        """
        return self.send({'cmd': 'SetToolbar', 'args': args})

    def UploadFITSFile(self, *args):
        """
        Upload the currently displayed FITS file to a proxy server

        call:

        JS9.UploadFITSFile()

        Upload the currently displayed FITS file to the proxy server, so
        back-end analysis can be performed. This routine requires that a
        Node.js-based JS9 helper is running and that the helper has enabled
        the loadProxy property and set up a workDir directory in which to
        store the FITS file.
        """
        return self.send({'cmd': 'UploadFITSFile', 'args': args})

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

    def DisplayNextImage(self, *args):
        """
        Display the Next (or Previous) Image

        call:

        JS9.DisplayNextImage(n)

        where:

        - n: number of images beyond (or prior to) the one currently displayed

        The JS9.DisplayNextImage() routine displays the nth image in
        the display's image list beyond the currently displayed image. The
        default value for n is 1. You can supply a negative number to
        display an image prior to the current one in the display's image list.
        """
        return self.send({'cmd': 'DisplayNextImage', 'args': args})

    def CreateMosaic(self, *args):
        """
        Create a Mosaic Image

        call:

        JS9.CreateMosaic(which, opts)

        where:

        - which: which images to use in the mosaic
        - opts: mosaic options

        The JS9.CreateMosaic() creates a mosaic image from the specified
        (previously-loaded) FITS images using the mProjectPP and mAdd programs
        form the Montage software suite. These Montage programs have been
        compiled into JS9 using Emscripten.

        Because the browser environment is memory-limited, there are some
        restrictions on generating mosaics in JS9. The FITS files must be
        well-behaved, i.e. they must have WCS projections which can be
        approximated by tangent-plane projections (TAN, SIN, ZEA, STG, ARC).
        This precludes creating mosaics from images covering large portions of
        the sky. For large sky areas, please use Montage itself on your desktop
        to create a mosaic. A simplified js9mosaic script is included in
        the JS9 distribution or, for more control, use the Montage programs
        directly. Of course, in either case, you must install Montage.

        The which parameter determine which images are used in the mosaic:

        - "current" or null: the current image in this display
        - "all": all images in this display
        - im: the image id an image from any display
        - [im1, im2, ...]: an array of image ids from any display

        Use "current" (or null) if you have loaded a multi-extension
        FITS mosaic into JS9. Use "all" if you have loaded several
        FITS files into JS9 and want to create a mosaic.

        In order to keep the size of the resulting mosaic within memory
        limits, JS9 reduces the size of each image before adding them all
        together The options parameter determines how the reduction is
        performed:

        - dim: size of mosaic (def: max of JS9.globalOpts.image.[xdim,ydim])
        - reduce: image size reduction technique: "js9" (def) or "shrink"
        - verbose: if true, processing output is sent to the javascript console

        The "dim" parameter is a target size: the larger of the resulting
        mosaic dimensions will be approximately this value, depending on how
        Montage processes the images. The "reduce" technique either runs
        internal JS9 image sectioning code (to produce smaller internal
        images, each of which are reprojected and added together) or runs the
        Montage mShrinkHdr code (which reprojects the full images into smaller
        files). The former seems to be faster than the latter in most
        cases. The "verbose" parameter will display output on the JavaScript
        console to let you know that the CreateMosaic() call is running
        properly.

        The resulting mosaic will be loaded into the specified JS9 display as
        a separate image. Because the mosaic is separate from the original
        image(s), you can view each of the latter individually (or view each
        image extension of a single image using the Extensions plugin).
        Internal analysis can be performed on the mosaic but,
        of course, no external analysis tasks will be available.
        """
        return self.send({'cmd': 'CreateMosaic', 'args': args})

    def ResizeDisplay(self, *args):
        """
        Change the width and height of the JS9 display

        call:

        JS9.ResizeDisplay(width, height)

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

    def GatherDisplay(self, *args):
        """
        Gather other images to this JS9 Display

        call:

        JS9.GatherDisplay(dname, opts)

        where:

        - dname: name of JS9 display to which the images will be gathered
        - opts: optional object

        You can supply an opts object containing the following properties:

        - images: array of image handles (or indexes into JS9.images array)
        to gather

        This routine move all or selected images in other displays to this
        display.
        """
        return self.send({'cmd': 'GatherDisplay', 'args': args})

    def SeparateDisplay(self, *args):
        """
        Separate images in this JS9 Display into new displays

        call:

        JS9.SeparateDisplay(dname, opts)

        where:

        - dname: name of JS9 display from which the images will be separated
        - opts: optional object for layout properties

        This routine moves each image in this display to a new display.
        You can supply an opts object containing the following properties:

        - images: array of image handles (or indexes into JS9.images array)
        to separate
        - layout: can be "horizontal", "vertical", "auto" (default: "auto")
        - leftMargin: margin in pixels between horizontally separated images
        - topMargin: margin in pixels between vertically separated images

        The "horizontal" layout will generate a single row of images. The
        "vertical" layout will generate a single column of images.  The "auto"
        option will layout the images in one or more rows. Each row will
        contain one or more images such that at least one-half of the
        right-most image is visible in the browser without the need for
        horizontal scrolling.
        """
        return self.send({'cmd': 'SeparateDisplay', 'args': args})

    def CenterDisplay(self, *args):
        """
        Scroll the JS9 display to the center of the viewport

        call:

        JS9.CenterDisplay()

        where:

        - dname: name of JS9 display to center

        This routine scrolls this display to the center of the viewport.
        """
        return self.send({'cmd': 'CenterDisplay', 'args': args})

    def CloseDisplay(self, *args):
        """
        Close all images in a display

        call:

        JS9.CloseDisplay(dname)

        where:

        - dname: name of JS9 display whose images will be closed

        This routine closes all images in the specified display.
        """
        return self.send({'cmd': 'CloseDisplay', 'args': args})

    def RenameDisplay(self, *args):
        """
        Rename the id of a JS9 display

        calling sequences:

        JS9.RenameDisplay(nid)        # change default id (usually "JS9") to nid
        JS9.RenameDisplay(oid, nid)   # change oid to nid

        where:

        - oid: old name of JS9 display
        - nid: new name of JS9 display

        This routine is used by the Desktop version of JS9 to implement the
        --title (and --renameid) switch(es), which change the id of the
        JS9 display(s) to the specified id(s). Once an id has been renamed,
        external communication (via the js9 script or pyjs9) should target
        the new id instead of the original id.

        The original id is still available internally, so Javascript public
        API calls on the web page itself can target either the original or
        the new id using the {display: "id"} syntax.
        """
        return self.send({'cmd': 'RenameDisplay', 'args': args})

    def RemoveDisplay(self, *args):
        """
        Close all images in a display and remove the display

        call:

        JS9.RemoveDisplay(dname)

        where:

        - dname:  name of JS9 display to remove

        This routine will close all images in the specified display and then
        remove the display. It is available for displays contained in
        light windows and for displays contained in JS9 Grid Containers. When
        removing the display inside a light window, the light window is
        immediately closed without a confirmation dialog box (unlike a light
        window being closed via its close button.) For a display inside
        a JS9 Grid Container, the display is removed from the DOM, so that it
        no longer is part of the grid layout. Note, however, that you cannot
        remove all displays from a grid container: at least one display must be
        left in the container.
        """
        return self.send({'cmd': 'RemoveDisplay', 'args': args})

    def DisplayHelp(self, *args):
        """
        Display help in a light window

        call:

        JS9.DisplayHelp(name)

        where:

        -  name: name of a help file or url of a web site to display

        The help file names are the property names in JS9.helpOpts (e.g.,
        'user' for the user page, 'install' for the install page, etc.).
        Alternatively, you can specify an arbitrary URL to display (just
        because).
        """
        return self.send({'cmd': 'DisplayHelp', 'args': args})

    def LightWindow(self, *args):
        """
        Display content in a light window

        call:

        JS9.LightWindow(id, type, content, title, opts)

        where:

        - id: unique id for light window div(default: "lightWindow" + uniqueID)
        - type: content type: "inline", "div", "ajax", "iframe" (def: "inline")
        - content: content of the light window (default: none)
        - title: title (default: "JS9 light window")
        - opts: configuration string
          (default: "width=830px,height=400px,center=1,resize=1,scrolling=1")

        Display arbitrary content inside a light window. There are any number
        of light window routines available on the Net. JS9 uses light window
        routines developed by Dynamic Drive (http://www.dynamicdrive.com).
        Extensive documentation can be found on the Dynamic Drive web
        site: http://www.dynamicdrive.com/dynamicindex8/dhtmlwindow.

        The content shown inside the window depends on the content parameter:

        -  iframe: the URL of the page to display (ie: "http://www.google.com")
        - inline: the HTML to display (back-slashing any special JavaScript
        characters, such as apostrophes)
        - ajax: the relative path to the external page to display, relative to
        the current page (ie: "../external.htm")
        - div: define a DIV element on the page with a unique ID attribute
        (probably hidden using style="display:none") and the use the DIV's id
        as the content value

        JS9 typically uses the inline option. Note that web sites often
        do not allow themselves to be embedded in an iframe, so this is an
        unreliable option.

        The opts parameter specifies options for the light window, such
        as its size.  This parameter consists of a string with comma-separated
        keywords, e.g.:

        >>> "width=830px,height=400px,center=1,resize=1,scrolling=1"

        The opts keywords, defined in the Dynamic Drive documentation, are:
        width, height, left, top, center, resize, and scrolling.  The
        JS9.lightOpts.dhtml object defines oft-used lightwin configurations,
        and the JS9.lightOpts.dhtml.textWin property is used as the
        default for this call. You can utilize these properties in your own
        call to LightWindow() or make up your own configuration string.

        As an extension to the Dynamic Drive light window support, JS9 adds
        the ability to double-click the title bar in order to close the window.
        """
        return self.send({'cmd': 'LightWindow', 'args': args})

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

    def helper(self, *args):
        """
        get helper info
        """
        return self.send({'cmd': 'helper', 'args': args})

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

    def regcnts(self, *args):
        """
        get background-subtracted counts in regions

        This is a commmand-style routine, easier to type than the full routine:
          - with no arguments, acts as if the Analysis menu option was chosen
          - with arguments, acts like the full routine

        With arguments, returned results are of type string.
        """
        return self.send({'cmd': 'regcnts', 'args': args})

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
