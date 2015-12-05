from setuptools import setup

def readme():
    with open('README.rst') as f:
        return f.read()

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
        'Topic :: Scientific/Engineering :: Astronomy',
      ],
      keywords='astronomy astrophysics image display',
      url='http://js9.si.edu',
      license='MIT',
      packages=['pyjs9'],
      install_requires=['six', 'requests'],
      extras_require={'all': ['numpy', 'astropy']},
      zip_safe=False)
