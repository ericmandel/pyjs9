"""Unit tests for pyjs9."""
import time
import subprocess
import pytest
from .. import JS9

JS9_TEST_URL = 'http://localhost:9000'

# https://gist.github.com/adamgoucher/3921739#comment-589432
@pytest.fixture(scope="module")
def js9_fixture(request):
    from selenium.webdriver import Firefox
    # request.instance.driver = Firefox()
    # request.instance.driver.get(JS9_TEST_URL)

    # The `pyjs9` tests need `jsHelper.js` running ...
    cmd = ['node', 'js9Helper.js']
    cmd = ['node', '/Users/deil/code/js9/js9Helper.js']
    jshelper = subprocess.Popen(cmd)
    # TODO: do we need to sleep here?
    # Sleep a bit to make sure that `js9Helper.js` has started up
    time.sleep(0.1)

    def fin():
        # request.instance.driver.quit
        jshelper.terminate()

    request.addfinalizer(fin)

# Starting `pyjs9` and `jsHelper.js` via the `js9_fixture`
# doesn't work for now ...
# def test_pyjs9(js9_fixture):
#     js9 = JS9(JS9_TEST_URL)
#     js9.SetColormap('red')


def test_pyjs9():
    js9 = JS9()
    js9.SetColormap('red')
