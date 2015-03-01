from setuptools import setup


def readme():
    with open('README.rst') as f:
        return f.read()

# http://pytest.org/latest/goodpractises.html#integration-with-setuptools-test-commands
import sys

from setuptools.command.test import test as TestCommand


class PyTest(TestCommand):
    user_options = [('pytest-args=', 'a', "Arguments to pass to py.test")]

    def initialize_options(self):
        TestCommand.initialize_options(self)
        self.pytest_args = []

    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        #import here, cause outside the eggs aren't loaded
        import pytest
        errno = pytest.main(self.pytest_args)
        sys.exit(errno)


setup(name='pyjs9',
      version='1.0',
      description='Python/JS9 connection, with numpy and astropy/fits support',
      long_description=readme(),
      author='Eric Mandel',
      author_email='saord@cfa.harvard.edu',
      classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Topic :: Scientific/Engineering :: Astronomy',
      ],
      keywords='astronomy astrophysics image display',
      url='http://js9.si.edu',
      license='MIT',
      packages=['pyjs9', 'pyjs9.extern'],
      zip_safe=False,
      tests_require=['pytest', 'selenium'],
      cmdclass={'test': PyTest},
)
