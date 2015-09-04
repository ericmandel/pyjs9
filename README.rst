JS9 brings image display right into your browser::

- display URL-based FITS images and binary tables
- drag and drop FITS images and binary tables
- change the colormap and scale
- manipulate the contrast/bias with the mouse
- display pixel values and WCS position information
- create and manipulate geometric regions of interest
- add your own extensions using plugins and the Public API
- perform data analysis (local and server-side)
- display RGB composite images
- control JS9 from the command line
- print images
- more to come!

See: http://js9.si.edu for more information about JS9.

pyjs9.py connects python and JS9 via the js9Helper.js back-end server::

- The JS9 class constructor connects to a single JS9 instance in a Web page.
- The JS9 object supports the JS9 Public API and a shorter command-line syntax.
- See: http://js9.si.edu/js9/help/publicapi.html for info about the public api
- Send/retrieve numpy arrays and astropy (or pyfits) hdulists to/from js9.

Requirements: pyjs9 utilizes the `requests
<http://www.python-requests.org/en/latest/>` module to communicate with a JS9
back-end Node server (which communicates with the browser itself).  The JS9
back-end server must be version 1.2 or higher.

Install from the repository using pip, as usual::

    > pip install git+https://github.com/ericmandel/pyjs9.git#egg=pyjs9

or from a local copy::

    > pip install /path/to/local/copy

Mandatory dependences::

    six
    requests

Optional dependences::

    numpy
    astropy

To run::

	> python
        ... (startup messages) ...
	>>> from pyjs9 import *
	>>> dir()
        ['JS9', ..., 'js9Globals']
	>>>
	>>> j = JS9()
	>>>
	>>> j.SetColormap('red')
	>>> j.GetColormap('red')
	{'bias': 0.5, 'colormap': 'red', 'contrast': 1}
	>>> j.cmap()
	'cool 1 0.5'
	>>>
	>>> hdul = j.GetFITS()
	>>> hdul.info()
	Filename: (No file associated with this HDUList)
	No.    Name         Type      Cards   Dimensions   Format
	0    PRIMARY     PrimaryHDU       6   (1024, 1024)   int32   
	>>>
	>>> narr = j.GetNumpy()
	>>> narr.shape
	(1024, 1024)

Or, if you have relatively fast internet connectivity, open the JS9 Web page
and::

	> python
        ... (startup messages) ...
	>>> from pyjs9 import *
	>>> dir()
        ['JS9', ..., 'js9Globals']
	>>>
	>>> j = JS9('js9.si.edu')
	>>> etc ...
