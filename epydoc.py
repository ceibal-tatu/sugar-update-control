#!/usr/bin/python
"""Simple script to run epydoc with the correct python path and arguments.

See http://epydoc.sourceforge.net/ for more information on epydoc."""

import sys, os.path
# Make sure that we don't get confused between an epydoc.py script and
# the real epydoc package.
if os.path.exists(os.path.join(sys.path[0], 'epydoc.py')):
    del sys.path[0]
# work around local installation quirks.
if False:
    # these aren't needed on debian/unstable; we'll add whatever
    # we need for the XO, Fedora, etc.
    sys.path.insert(0, '/usr/share/pycentral/python-roman/site-packages')
    sys.path.insert(0, '/usr/share/pycentral/python-docutils/site-packages')
    sys.path.insert(0, '3rdParty/epydoc/src')

from epydoc import gui, cli
if '-g' in sys.argv:
    sys.argv.remove('-g')
    gui.gui()
else:
    sys.argv.insert(1, '--config')
    sys.argv.insert(2, 'epydoc.config')
    cli.cli()
