#!/usr/bin/python
from DistUtilsExtra.command import * # gettext support
from distutils.core import setup

setup(name='sugar-update-control',
      version="0.27",
      description='Sugar update control panel',
      author='C. Scott Ananian',
      author_email='cscott@laptop.org',
      url='http://dev.laptop.org/git/users/cscott/sugar-update-control',
      data_files=[('share/sugar/extensions/cpsection/updater',
                   ['src/__init__.py', 'src/model.py', 'src/view.py']),
                  ],
      cmdclass = { "build" : build_extra.build_extra,
                   "build_i18n" :  build_i18n.build_i18n },
      )
