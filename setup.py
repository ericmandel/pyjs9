from setuptools import setup

def readme():
    with open('README.rst') as f:
        return f.read()

setup(name='pyjs9',
      version='3.8',
      description='Python/JS9 connection, with numpy and astropy/fits support',
      long_description=readme(),
      author='Eric Mandel',
      author_email='saord@cfa.harvard.edu',
      classifiers=[
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Topic :: Scientific/Engineering :: Astronomy',
      ],
      keywords='astronomy astrophysics image display',
      url='https://js9.si.edu',
      license='MIT',
      packages=['pyjs9'],
      install_requires=['requests'],
      extras_require={'all': ['numpy', 'astropy']},
      zip_safe=False)
