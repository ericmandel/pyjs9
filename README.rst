**My software career comes to an end on July 1, 2022 and with it will end active development of pyjs9. Please plan accordingly. Eric**

.. image:: https://zenodo.org/badge/DOI/10.5281/zenodo.6675877.svg
   :target: https://doi.org/10.5281/zenodo.6675877

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
- much more ...

See: https://js9.si.edu for more information about JS9.

pyjs9.py connects Python and JS9 via the js9Helper.js back-end server::

- The JS9 class constructor connects to a single JS9 instance in a Web page.
- The JS9 object supports the JS9 Public API and a shorter command-line syntax.
- See: https://js9.si.edu/js9/help/publicapi.html for info about the public api
- Send/retrieve numpy arrays and astropy (or pyfits) hdulists to/from JS9.

Requirements: Python 3.5 or higher is required. Beyond that, pyjs9
communicates with a JS9 back-end Node server (which communicates with
the browser itself). By default, pyjs9 utilizes the `requests
<http://www.python-requests.org/en/latest/>` module to communicate
with the JS9 back-end server. However, if you install
`python-socketio` <https://pypi.org/project/python-socketio>`, pyjs9
will use the faster, persistent `socket.io http://socket.io/`
protocol.

Install from the repository using pip3, as usual::

    > pip3 install git+https://github.com/ericmandel/pyjs9.git#egg=pyjs9

or from a local copy::

    > pip3 install /path/to/local/copy

Mandatory dependencies::

    requests

Optional dependencies::

    numpy               # support for GetNumpy and SetNumpy methods
    astropy             # support for GetFITS and SetFITS methods
    python-socketio     # fast, persistent socket.io protocol, instead of html
                        # (install version 5.x, version 4.x is deprecated)

To run::

        > # ensure JS9 node-server is running ...
        > # visit your local JS9 Web page in your browser ...
	> python
        ... (startup messages) ...
	>>> import pyjs9
	>>>
	>>> j = pyjs9.JS9()        # default: connect to 'http://localhost'
	>>>
	>>> j.GetColormap()
	{'bias': 0.5, 'colormap': 'grey', 'contrast': 1}
	>>> j.SetColormap('red')
	>>> j.cmap()
	'red 1 0.5'
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

If you have internet connectivity, visit the JS9 Web page at
https://js9.si.edu with your browser and::

	> python
        ... (startup messages) ...
	>>> import pyjs9
	>>>
	>>> j = pyjs9.JS9('https://js9.si.edu')        # connect to JS9 Web site
	>>>
	>>> j.GetColormap()
	{'bias': 0.5, 'colormap': 'grey', 'contrast': 1}
	>>>
	>>> # etc ...
