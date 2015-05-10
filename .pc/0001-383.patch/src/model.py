#!/usr/bin/python
# Copyright (C) 2008 One Laptop Per Child Association, Inc.
# Licensed under the terms of the GNU GPL v2 or later; see COPYING for details.
# Written by C. Scott Ananian <cscott@laptop.org>
"""Activity updater: backing model.

This module implements the non-GUI portions of the activity updater,
including in particular the master list of groups, activities, whether
updates are needed, and the URL at which to find the updated activity.

Because `UpdateList` inherits from `Gtk.ListStore` so that it plays
nicely with a GUI, this module requires `gtk`.  Those of you without
DISPLAY set will have to put up with a GtkWarning that the display
couldn't be opened.  Sorry.
"""
from __future__ import with_statement
from __future__ import division

# for testing
_DEBUG_MAKE_ALL_OLD = False
_DEBUG_CHECK_VERSIONS = False

# default timeout for HTTP connections, in seconds
HTTP_TIMEOUT=30

from gi.repository import Rsvg
from gi.repository import Gtk
from gi.repository import GdkPixbuf
from gi.repository import GObject

import locale
import os
import os.path
import socket
import sys
import traceback
import zipfile
from HTMLParser import HTMLParseError
from urllib2 import HTTPError

import gettext
_ = lambda msg: gettext.dgettext('sugar-update-control', msg)

import bitfrost.update.actinfo as actinfo
import bitfrost.update.actutils as actutils
import bitfrost.update.microformat as microformat
import bitfrost.util.urlrange as urlrange

from sugar3.bundle.bundleversion import NormalizedVersion

# weak dependency on inhibit_suspend from olpc-update package
try:
    from bitfrost.update import inhibit_suspend
except ImportError:
    # use a no-op substitude for the inhibit_suspend decorator.
    inhibit_suspend = lambda f: f

# lifted from gnome-update-manager/Common/utils
def _humanize_size(bytes):
    """
    Convert a given size in bytes to a nicer better readable unit
    """
    if bytes == 0:
        # TRANSLATORS: download size is 0
        return _("None")
    elif bytes < 1024:
        # TRANSLATORS: download size of very small updates
        return _("1 KB")
    elif bytes < 1024 * 1024:
        # TRANSLATORS: download size of small updates, e.g. "250 KB"
        return locale.format_string(_("%.0f KB"), bytes/1024)
    else:
        # TRANSLATORS: download size of updates, e.g. "2.3 MB"
        return locale.format_string(_("%.1f MB"), bytes / 1024 / 1024)

def _svg2pixbuf(icon_data):
    """Convert the given `icon_data` SVG string to a `GdkPixbuf.Pixbuf`
    with maximum size 55x55."""
    import re
    # substitute black/white for icon color entities.
    for entity, value in [('stroke_color','#808080'),
                          ('fill_color','#eee')]:
        xml = '<!ENTITY %s "%s">' % (entity, value) 
        icon_data = re.sub('<!ENTITY %s .*>' % entity, xml, icon_data) 
    h = Rsvg.Handle.new_from_data(icon_data)
    if h.get_property('width') > 55 or h.get_property('height') > 55:
        # lame! scale it.
        print "WARNING: oversize icon (%dx%d), scaling." % \
              (h.get_property('width'), h.get_property('height'))
        del h
        pbl = GdkPixbuf.PixbufLoader()
        pbl.set_size(55, 55)
        pbl.write(icon_data)
        pbl.close()
        return pbl.get_pixbuf()
    return h.get_pixbuf()

_parse_cache = {}
def _check_for_updates(url, activity_id):
    """Downloads the given URL, parses it (caching the result), and returns
    a list of (version, url) pairs present for the given `activity_id`.
    Returns `None` if there was a problem downloading the URL.
    Returns a zero-length list if the given URL is unparsable or does not
    contain information for the desired activity_id."""
    global _parse_cache
    if url not in _parse_cache:
        try:
            __, __, _parse_cache[url] = \
                microformat.parse_url(url, timeout=HTTP_TIMEOUT)
            if _DEBUG_CHECK_VERSIONS:
                # for kicks and giggles, verify these version #s!
                for n_activity_id, versions in _parse_cache[url].items():
                    for ver, url2 in versions:
                        actual_id, actual_ver = \
                                   actutils.id_and_version_from_url(url2)
                        if actual_id != n_activity_id:
                            print "ACTIVITY ID SHOULD BE", n_activity_id, \
                                  "BUT ACTUALLY IS", actual_id, ("(%s)"%url2)
                        if NormalizedVersion(actual_ver) != NormalizedVersion(ver):
                            print "VERSION SHOULD BE", ver, \
                                  "BUT ACTUALLY IS", actual_ver, ("(%s)"%url2)
        except HTMLParseError:
            _parse_cache[url] = {} # parse error
        except (IOError, socket.error):
            _parse_cache[url] = None # network error
    activity_map = _parse_cache[url]
    if activity_map is None: return None # error attempting to check.
    if activity_id not in activity_map: return [] # no versions found.
    return activity_map[activity_id]

def _retrieve_update_version(actbun,
                             network_success_cb=(lambda url: None),
                             network_failure_cb=(lambda url: None)):
    """Return a tuple of current version, new version, url for new
    version, size of new version, given a current activity bundle.
    All the information about the new version is `None` if no newer
    update can be found.  The optional `network_success_cb` is invoked
    with a single `url` parameter whenever a network operation
    succeeds, and the optional `network_failure_cb` is invoked with a
    single `url` parameter if all fallback network operations for a
    given activity bundle fail."""
    update_url = actbun.get_update_url()
    oldv = 0 if _DEBUG_MAKE_ALL_OLD else actbun.get_activity_version()
    # here we implement the search for a release-specific version
    # XXX: would be nice to use retrieve_first_variant here
    vlist = None
    for uu in actinfo.url_variants(update_url):
        vlist = _check_for_updates(uu, actbun.get_bundle_id())
        if vlist is not None:
            network_success_cb(uu)
            if len(vlist) > 0:
                break # found some info
    if vlist is None:
        # all url variants for the given actbun have failed.
        network_failure_cb(update_url)
        return oldv, None, None, 0
    newv, newu = microformat.only_best_update(vlist + [(oldv, None)])
    if newu is None or newv==oldv: return oldv, None, None, 0 # no updates
    # find size of new version.
    try:
        size = urlrange.urlopen(newu, timeout=HTTP_TIMEOUT).length()
        return oldv, newv, newu, size
    except (HTTPError, IOError, socket.error):
        # hmm, i guess that url isn't valid after all.  bail.
        return oldv, None, None, 0 # there are no *actual* updates.

##########################################################################
# Fundamental data object: the real work gets done here.

_column_name_map = dict(globals())
ACTIVITY_ID, ACTIVITY_BUNDLE, ACTIVITY_ICON, \
             UPDATE_URL, UPDATE_VERSION, UPDATE_SIZE, \
             UPDATE_EXISTS, UPDATE_SELECTED, \
             DESCRIPTION_BIG, DESCRIPTION_SMALL, IS_HEADER, GROUP_NUM = \
             xrange(12)
"""List of columns in the `UpdateList`."""
_column_name_map = dict((k,v) for k,v in globals().items()
                        if k not in _column_name_map and k!='_column_name_map')
"""Mapping from column names to indices."""

class UpdateList(Gtk.ListStore):
    """Model which provides backing storage for the activity list treeview."""
    __gproperties__ = {
        'is_valid': (GObject.TYPE_BOOLEAN, 'is valid',
                     'true iff the UpdateList has been properly refreshed',
                     False, GObject.PARAM_READABLE),
        'saw_network_failure': (GObject.TYPE_BOOLEAN, 'saw network failure',
                                'true iff at least one network IO error '+
                                'occurred when the UpdateList was last '+
                                'refreshed',
                                False, GObject.PARAM_READABLE),
        'saw_network_success': (GObject.TYPE_BOOLEAN, 'saw network success',
                                'true iff at least one network operation '+
                                'completed successfully when the UpdateList '+
                                'was last refreshed',
                                False, GObject.PARAM_READABLE),
    }

    def __init__(self, skip_icons=False):
        Gtk.ListStore.__init__(self,
                               # column types
                               str, object, GdkPixbuf.Pixbuf,
                               str, str, long,
                               bool, bool, str, str, bool, int)
        self._skip_icons = skip_icons
        self._is_valid = False
        self._network_failures = []
        self._saw_network_success = False

    def __del__(self):
        """Free up any memory held by the cache in urlrange."""
        urlrange.urlcleanup()

    def _append(self, at=None, **kwargs):
        """Utility function to make it easier to add rows and get paths."""
        global _column_name_map
        # defaults for each column
        row = [None, None, None,
               None, "0", 0,
               True, True, None, None, False, 0]
        # set entries in the row based on kwargs
        for k,v in kwargs.items():
            row[_column_name_map[k]] = v
        if at is not None:
            it = self.insert(at, row)
        else:
            it = self.append(row)
        return self.get_path(it)

    def toggle_select(self, path):
        """Toggle whether the given update will be installed."""
        row = self[path]
        row[UPDATE_SELECTED] = not row[UPDATE_SELECTED]

    # don't touch the UI in refresh, it needs to be thread-safe.
    # (model will be disconnected from the view before invoking refresh
    #  in another thread)
    def refresh(self, progress_callback=lambda n, extra: None,
                clear_cache=True):
        """Perform network operations to find available updates.

        The `progress_callback` is invoked with numbers between 0 and 1
        or `None` as the network queries complete.  The last callback will be
        `progress_callback(1)`.  Passing `None` to `progress_callback`
        requests "pulse mode" from the progress bar.
        """
        global _parse_cache
        if clear_cache:
            _parse_cache = {} # clear microformat parse cache
            urlrange.urlcleanup() # clean url cache
        self._cancel = False
        self._invalidate()
        # don't notify for the following; we'll notify at the end when we
        # know what the new values ought to be.
        self._saw_network_success = False
        self._network_failures = []
        # bookkeeping
        progress_callback(None, None)
        self.clear()
        # find all activities already installed.
        progress_callback(None, _('Looking for local activities and content...')) # pulse
        activities = actinfo.get_activities() + actinfo.get_libraries()
        # enumerate all group urls
        progress_callback(None, _('Loading groups...'))
        group_urls = actinfo.get_activity_group_urls()
        # now we've got enough information to allow us to compute a
        # reasonable completion percentage.
        steps_total = [ len(activities) + len(group_urls) + 3 ]
        steps_count = [ 0 ] # box this to allow update from mkprog.
        def mkprog(msg=None):
            """Helper function to do progress update."""
            steps_count[0] += 1
            progress_callback(steps_count[0]/steps_total[0], msg)
        mkprog(_('Loading groups...'))
        # okay, first load up any group definitions; these take precedence
        # if present.
        groups = []
        def group_parser(f, url):
            name, desc, groups = microformat.parse_html(f.read(), url)
            if len(groups) > 0 or (name is not None and desc is not None):
                return name, desc, groups
            return None # hmm, not a successful parse.
        for gurl in group_urls:
            mkprog(_('Fetching %s...') % gurl)
            if self._cancel: break # bail!
            gdata = actinfo.retrieve_first_variant(gurl, group_parser,
                                                   timeout=HTTP_TIMEOUT)
            if gdata is not None:
                gname, gdesc, gactmap = gdata
                groups.append((gname, gdesc, gurl, gactmap))
                self._saw_network_success = True
            else:
                # headers even for failed groups.
                groups.append((None, gurl, gurl, {}))
                self._network_failures.append(gurl)
        # now start filling up the liststore, keeping a map from activity id
        # to liststore path
        row_map = {}
        group_num = 0
        for gname, gdesc, gurl, gactmap in groups:
            # add group header.
            if gname is None: gname = _('Activity Group')
            self._append(IS_HEADER=True,
                         UPDATE_URL=gurl,
                         GROUP_NUM=group_num,
                         DESCRIPTION_BIG=gname,
                         DESCRIPTION_SMALL=gdesc)
            # now add entries for all activities in the group, whether
            # currently installed or not.
            for act_id, version_list in sorted(gactmap.items()):
                version, url = microformat.only_best_update(version_list)
                if act_id not in row_map:
                    # temporary description in case user cancels the refresh
                    tmp_desc = act_id.replace('sugar-is-lame',
                                              'lame-is-the-new-cool')
                    row_map[act_id] = self._append(ACTIVITY_ID=act_id,
                                                   GROUP_NUM=group_num,
                                                   UPDATE_EXISTS=True,
                                                   UPDATE_URL=url,
                                                   UPDATE_VERSION=str(version),
                                                   DESCRIPTION_BIG=tmp_desc)
                    steps_total[0] += 1 # new activity?
                else:
                    # allow for a later version in a different group
                    row = self[row_map[act_id]]
                    if NormalizedVersion(version) > \
                            NormalizedVersion(row[UPDATE_VERSION]):
                        row[UPDATE_URL] = url
                # XXX: deal with pinned updates.
            group_num += 1
        # add in information from local activities.
        self._append(IS_HEADER=True, GROUP_NUM=group_num,
                     DESCRIPTION_BIG=_('Local activities'))
        for act in activities:
            act_id = act.get_bundle_id()
            if act_id not in row_map:
                row_map[act_id] = self._append(ACTIVITY_ID=act_id,
                                               GROUP_NUM=group_num,
                                               UPDATE_EXISTS=False)
            else:
                steps_total[0] -= 1 # correct double-counting.
            # update icon, and bundle
            row = self[row_map[act_id]]
            row[ACTIVITY_BUNDLE] = act
            row[DESCRIPTION_BIG] = act.get_name()
            if not self._skip_icons:
                try:
                    row[ACTIVITY_ICON] = _svg2pixbuf(act.get_icon_data())
                except IOError:
                    # dlo trac #8149: don't kill updater if existing icon
                    # bundle is malformed.
                    pass
        group_num += 1
        # now do extra network traffic to look for actual updates.
        def refresh_existing(row):
            """Look for updates to an existing activity."""
            act = row[ACTIVITY_BUNDLE]
            oldver = 0 if _DEBUG_MAKE_ALL_OLD else act.get_activity_version()
            size = 0
            def net_good(url_): self._saw_network_success = True
            def net_bad(url): self._network_failures.append(url)

            # activity group entries have UPDATE_EXISTS=True
            # for any activities not present in the group, try their update_url
            # (if any) for new updates
            # note the behaviour here: if the XS (which hosts activity groups)
            # has an entry for the activity, then we trust that it is the
            # latest and we don't go online to check.
            # we only go online for activities which the XS does not know about
            # the purpose of this is to reduce the high latency of having
            # to check multiple update_urls on a slow connection.

            if row[UPDATE_EXISTS]:
                # trust what the XS told us
                newver, newurl = row[UPDATE_VERSION], row[UPDATE_URL]
            else:
                # hit the internet for updates
                oldver, newver, newurl, size = \
                    _retrieve_update_version(act, net_good, net_bad)

            # make sure that the version we found is actually newer...
            if newver is not None and NormalizedVersion(newver) <= \
                    NormalizedVersion(act.get_activity_version()):
                newver = None
            elif row[UPDATE_EXISTS]:
                # since we trusted the activity group page above, we don't
                # know the size of this bundle. but if we're about to offer it
                # as an update then we should look that up now, with an HTTP
                # request.
                # (by avoiding a load of HTTP requests on activity versions that
                #  we already have, we greatly increase the speed and usability
                #  of this updater on high-latency connections)
                size = urlrange.urlopen(row[UPDATE_URL], timeout=HTTP_TIMEOUT)\
                       .length()

            row[UPDATE_EXISTS] = (newver is not None)
            row[UPDATE_URL] = newurl
            row[UPDATE_SIZE] = size
            if newver is None:
                description = _('At version %s') % oldver
            else:
                description = \
                    _('From version %(old)s to %(new)s (Size: %(size)s)') % \
                    { 'old':oldver, 'new':newver, 'size':_humanize_size(size) }
                row[UPDATE_SELECTED] = True
            row[DESCRIPTION_SMALL] = description
        def refresh_new(row):
            """Look for updates to a new activity in the group."""
            uo = urlrange.urlopen(row[UPDATE_URL], timeout=HTTP_TIMEOUT)
            row[UPDATE_SIZE] = uo.length()
            zf = zipfile.ZipFile(uo)
            # grab data from activity.info file
            activity_base = actutils.bundle_base_from_zipfile(zf)
            try:
                zf.getinfo('%s/activity/activity.info' % activity_base)
                is_activity = True
            except KeyError:
                is_activity = False
            if is_activity:
                cp = actutils.activity_info_from_zipfile(zf)
                SECTION = 'Activity'
            else:
                cp = actutils.library_info_from_zipfile(zf)
                SECTION = 'Library'
            act_id = None
            for fieldname in ('bundle_id', 'service_name', 'global_name'):
                if cp.has_option(SECTION, fieldname):
                    act_id = cp.get(SECTION, fieldname)
                    break
            if not act_id:
                raise RuntimeError("bundle_id not found for %s" %
                                   row[UPDATE_URL])
            name = act_id
            if cp.has_option(SECTION, 'name'):
                name = cp.get(SECTION, 'name')
            # okay, try to get an appropriately translated name.
            if is_activity:
                lcp = actutils.locale_activity_info_from_zipfile(zf)
                if lcp is not None:
                    name = lcp.get(SECTION, 'name')
            else:
                s = actutils.locale_section_for_content_bundle(cp)
                if s is not None and cp.has_option(s, 'name'):
                    name = cp.get(s, 'name')
            version = None
            for fieldname in ('activity_version', 'library_version'):
                if cp.has_option(SECTION, fieldname):
                    version = cp.get(SECTION, fieldname)
                    break
            if version is None:
                raise RuntimeError("can't find version for %s" %
                                   row[UPDATE_URL])
            row[DESCRIPTION_BIG] = name
            row[DESCRIPTION_SMALL] = \
                _('New version %(version)s (Size: %(size)s)') % \
                {'version':version, 'size':_humanize_size(row[UPDATE_SIZE])}
            # okay, let's try to update the icon!
            if not self._skip_icons:
                if is_activity:
                    # XXX should failures here kill the upgrade?
                    icon_file = cp.get(SECTION, 'icon')
                    icon_filename = '%s/activity/%s.svg'%(activity_base, icon_file)
                    row[ACTIVITY_ICON] = _svg2pixbuf(zf.read(icon_filename))
                else:
                    row[ACTIVITY_ICON] = _svg2pixbuf(actinfo.DEFAULT_LIBRARY_ICON)
        # go through activities and do network traffic
        for row in self:
            if self._cancel: break # bail!
            if row[IS_HEADER]: continue # skip
            # skip journal
            if row[ACTIVITY_ID] == "org.laptop.JournalActivity": continue
            mkprog(_('Checking %s...') % row[DESCRIPTION_BIG])
            try:
                if row[ACTIVITY_BUNDLE] is None:
                    refresh_new(row)
                    self._saw_network_success = True
                else:
                    refresh_existing(row)
            except:
                row[UPDATE_EXISTS] = False # something wrong, can't update
                if row[UPDATE_URL] is not None:
                    self._network_failures.append(row[UPDATE_URL])
                # log the problem for later debugging.
                print "Failure updating", row[DESCRIPTION_BIG], \
                      row[DESCRIPTION_SMALL], row[UPDATE_URL]
                traceback.print_exc()
        mkprog('Sorting...') # all done
        # hide headers if all children are hidden
        sawone, last_header = False, None
        for row in self:
            if row[IS_HEADER]:
                if last_header is not None:
                    last_header[UPDATE_EXISTS] = sawone
                sawone, last_header = False, row
            elif row[UPDATE_EXISTS]:
                sawone = True
        if last_header is not None:
            last_header[UPDATE_EXISTS] = sawone
        # finally, sort all rows.
        self._sort()
        mkprog() # all done
        # XXX: check for base os update, and add an entry here?
        self._is_valid = True
        self.notify('is-valid')
        self.notify('saw-network-failure')
        self.notify('saw-network-success')

    def _sort(self):
        """Sort rows by group number, then case-insensitively by description."""
        l = lambda s: s.decode('utf-8','ignore').lower() \
            if s is not None else None
        def sort_value(row_num):
            row = self[row_num]
            return (row[GROUP_NUM], 0 if row[IS_HEADER] else 1,
                    l(row[DESCRIPTION_BIG]), l(row[DESCRIPTION_SMALL]))
        row_nums = range(len(self))
        row_nums.sort(key=sort_value)
        self.reorder(row_nums)

    def cancel_refresh(self):
        """Asynchronously cancel a `refresh` operation."""
        self._cancel = True

    def cancel_download(self):
        """Asynchronously cancel a `download_selected_updates` operation."""
        self._cancel = True

    def _sum_rows(self, row_func):
        """Sum the values returned by row_func called on all non-header
        rows."""
        return sum(row_func(r) for r in self if not r[IS_HEADER])

    def updates_available(self):
        """Return the number of updates available.

        Updated by `refresh`."""
        return self._sum_rows(lambda r: 1 if r[UPDATE_EXISTS] else 0)

    def updates_selected(self):
        """Return the number of updates selected."""
        return self._sum_rows(lambda r: 1 if
                              r[UPDATE_EXISTS] and r[UPDATE_SELECTED] else 0)
    def updates_size(self):
        """Returns the size (in bytes) of the selected updates available.

        Updated by `refresh`."""
        return self._sum_rows(lambda r: r[UPDATE_SIZE] if
                              r[UPDATE_EXISTS] and r[UPDATE_SELECTED] else 0)
    def unselect_all(self):
        """Unselect all available updates."""
        for row in self:
            if not row[IS_HEADER]:
                row[UPDATE_SELECTED] = False

    def select_all(self):
        """Select all available updates."""
        for row in self:
            if not row[IS_HEADER]:
                row[UPDATE_SELECTED] = True

    def is_valid(self):
        """The UpdateList is invalidated before it is refreshed, and when
        the group information is modified without refreshing."""
        return self._is_valid

    def _invalidate(self):
        """Set the 'is-valid' property to False and notify listeners."""
        if self._is_valid:
            # don't notify if already invalid
            self._is_valid = False
            self.notify('is-valid')

    def saw_network_failure(self):
        """Returns true iff there was at least one network failure during the
        last refresh operation."""
        return len(self._network_failures) > 0

    def saw_network_success(self):
        """Returns true iff there was at least one successful network
        transaction during the last refresh operation."""
        return self._saw_network_success

    def do_get_property(self, prop):
        """Standard interface to access the 'is-valid' property."""
        if prop.name == 'is-valid': return self.is_valid()
        if prop.name == 'saw-network-failure': return self.saw_network_failure()
        if prop.name == 'saw-network-success': return self.saw_network_success()
        raise AttributeError, 'unknown property %s' % prop.name

    def add_group(self, group_url):
        """Add the group referenced by the given `group_url` to the end of the
        groups list.  This invalidates the UpdateList; you'll have to call
        the refresh method to revalidate."""
        # sanity check
        if not group_url.strip():
            return False # nothing to it
        # double check that group not already present.
        for row in self:
            if row[IS_HEADER] and row[UPDATE_URL] == group_url:
                return False # already there.
        # find the group number we should use for this new group.
        new_gnum = None
        for row in self:
            if row[IS_HEADER] and row[UPDATE_URL] is None:
                new_gnum = row[GROUP_NUM]
            if new_gnum is not None and row[GROUP_NUM] >= new_gnum:
                # renumber to make room.
                row[GROUP_NUM] += 1
        # add the new group!
        self._append(at=0,
                     IS_HEADER=True,
                     GROUP_NUM = new_gnum,
                     DESCRIPTION_BIG=_('New group'),
                     DESCRIPTION_SMALL=group_url,
                     UPDATE_URL=group_url)
        self._sort()
        self._write_groups()
        # invalid: need to refresh to update activities.
        self._invalidate()
        return True

    def del_group(self, group_url):
        """Delete all entries associated with the group identified by the
        given `group_url`.  This invalidates the UpdateList; you'll have to
        call the refresh method to revalidate."""
        group_num = None
        # YUCK!  Removing a group of rows in a tree model is *ugly*.
        row = self.get_iter_first()
        while row is not None:
            remove = False
            if group_num is None:
                if self[row][IS_HEADER] and self[row][UPDATE_URL] == group_url:
                    group_num = self[row][GROUP_NUM]
                    remove = True
            elif self[row][GROUP_NUM] == group_num:
                remove = True
            else:
                self[row][GROUP_NUM] -= 1
            if not remove:
                row = self.iter_next(row)
            elif not self.remove(row):
                row = None # removed last row.
        if group_num is None:
            return False # not found.
        self._sort()
        self._write_groups()
        self._invalidate()
        return True

    def move_group(self, group_url, desired_num=-1):
        """Move the group with the given UPDATE_URL to the specified location.
        The location is specified *before* the existing row for the group is
        deleted, so the final location may vary.  If desired_num is less than
        0, then the row is moved to the very end."""
        initial_num, max_num = None, None
        # find the row, first.
        for row in self:
            if row[IS_HEADER]:
                max_num = row[GROUP_NUM]
                if row[UPDATE_URL] == group_url:
                    initial_num = row[GROUP_NUM]
        if initial_num is None or max_num is None:
            return False # can't find it, or there are no rows.
        # now make a mapping to define the desired reordering.
        if desired_num < 0: desired_num = max_num
        if desired_num == initial_num: return False # no change
        if desired_num > initial_num: desired_num -= 1
        def make_mapping():
            new_nums = list(xrange(max_num))
            del new_nums[initial_num]
            new_nums.insert(desired_num, initial_num)
            new_nums.append(max_num)
            return new_nums
        m = dict(zip(make_mapping(), xrange(max_num+1)))
        # reset the group numbers appropriately
        for row in self:
            row[GROUP_NUM] = m[row[GROUP_NUM]]
        # sort to actually reorder
        self._sort()
        # save these changes
        self._write_groups()
        # not valid because activities may need group reassignment.
        self._invalidate()
        return True

    def _write_groups(self):
        """Write a new user groups file based on the current model."""
        with open(actinfo.USER_GROUPS_FILE, 'w') as f:
            for row in self:
                if row[IS_HEADER] and row[UPDATE_URL] is not None:
                    print >>f, row[UPDATE_URL]

    def download_selected_updates(self, progress_cb=(lambda n, row: None),
                                  dir=None):
        """Return a generator giving (row, local filename) pairs for
        each selected update.  Caller is responsible for unlinking
        these files when they are through.  The `progress_cb` gets a
        floating point number in [0, 1] indicating the amount of the
        download which is complete, and a reference to the row in this
        `UpdateList` describing the current download.  The files will
        be created in the directory specified by `dir`, if it is
        given, otherwise they will be created in the user's activity
        directory.  If the remote HTTP server does not provide information
        about the length of some of the downloads, the first parameter
        to the `progress_cb` will be `None` during those downloads.
        """
        if dir is None: dir=actinfo.USER_ACTIVITY_DIR
        if not os.path.isdir(dir): os.makedirs(dir)
        self._cancel = False
        sizes = [ 0, self.updates_size() ]
        for row in self:
            if self._cancel: return # bail.
            if row[IS_HEADER]: continue
            if not (row[UPDATE_EXISTS] and row[UPDATE_SELECTED]): continue
            def report(cursize, totalsize_):
                if totalsize_ is None:
                    progress_cb(None, row)
                else:
                    progress_cb((sizes[0]+cursize) / sizes[1], row)
            try:
                yield row, urlrange.urlretrieve(row[UPDATE_URL], dir=dir,
                                                reporthook=report,
                                                cancelhook=lambda:self._cancel,
                                                store_cache=False,
                                                timeout=HTTP_TIMEOUT)
            except urlrange.CancelException:
                return # bail.
            except (HTMLParseError, IOError, socket.error):
                yield row, None # network error
            # XXX: if rows eventually have 'indeterminate size', then we
            #      should update this progress computation, probably based
            #      on the size of the file we just yielded above!
            sizes[0] += row[UPDATE_SIZE]
            progress_cb(sizes[0] / sizes[1], row)


#########################################################################
# Control panel keys for command-line and GUI use.

# note that the *presence* of a key is defined by the presence of a
# get_<key> method, the *documentation* for a key is given by the
# doc string of the set_<key> method, but actually looking at
# values from the command-line is performed using print_<key>.

def _print_status(n, extra):
    """Returns a helper function which print a human-readable
    completion percentage."""
    sys.stdout.write(' '*79)
    sys.stdout.write('\r')
    if extra is None: extra=''
    if n is None:
        sys.stdout.write('%s\r' % extra[:77].ljust(77))
    else:
        sys.stdout.write('%s %5.1f%%\r' % (extra[:70].ljust(70), n*100))
    sys.stdout.flush()

# 'available_updates' key -----------------------------
def get_available_updates():
    # only here to get this key to show up for '-l'
    raise ValueError('This method not used by the GUI')

@inhibit_suspend
def print_available_updates():
    """Print the set of available updates in nice human-readable fashion."""
    ul = UpdateList(skip_icons=True)
    ul.refresh(_print_status)
    print
    def opt(x):
        if x is None or x == '': return ''
        return ': %s' % x
    for row in ul:
        if not row[UPDATE_EXISTS]: continue # skip
        if row[IS_HEADER]:
            print row[DESCRIPTION_BIG] + opt(row[DESCRIPTION_SMALL])
        else:
            print '*', row[DESCRIPTION_BIG] + opt(row[DESCRIPTION_SMALL])
    print
    print _('%(number)d updates available.  Size: %(size)s') % \
          { 'number': ul.updates_available(),
            'size': _humanize_size(ul.updates_size()) }

def set_available_updates():
    # NOTE slightly odd way to document this control panel key
    """Retrieve the list of available activity updates."""
    raise ValueError(_("Setting the list of updates is not permitted."))

# 'install_updates' key -----------------------------
def get_install_update():
    # this func is needed to make the 'install_update' key show up for '-l'
    raise ValueError(_("Only the 'set' operation for this key is defined."))

@inhibit_suspend
def set_install_update(which):
    """
    Install any available updates for the activity identified by the
    given (localized) name or bundle id, or all available updates if
    'all' is given.
    """
    import os
    ul = UpdateList(skip_icons=True)
    ul.refresh(_print_status)
    def too_many():
        raise ValueError(_('More than one match found for the given activity name or id.'))
    def no_updates():
        raise ValueError(_('The given activity is already up-to-date.'))
    if which != 'all':
        found = False
        for row in ul:
            row[UPDATE_SELECTED] = False
        for row in ul:
            if row[IS_HEADER]: continue
            if which==row[ACTIVITY_ID]:
                if found: too_many()
                found = True
                if not row[UPDATE_EXISTS]: no_updates()
                row[UPDATE_SELECTED] = True
        if not found:
            found_but_uptodate=False
            for row in ul:
                if row[IS_HEADER]: continue
                if which in row[DESCRIPTION_BIG]:
                    if not row[UPDATE_EXISTS]: # could be false match
                        found_but_uptodate=True
                        continue
                    if found: too_many()
                    found = True
                    row[UPDATE_SELECTED] = True
        if not found:
            if found_but_uptodate: no_updates()
            raise ValueError(_('No activity found with the given name or id.'))
        assert ul.updates_selected() == 1
    # okay, now we've selected only our desired updates.  Download and
    # install them!
    # we need to set up a glib event loop in order to connect to the
    # activity registry (sigh) in ActivityBundle.upgrade() below.
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    # we'll fetch the main loop, but we don't actually have to run it.
    loop = GObject.MainLoop()
    from jarabe.model.bundleregistry import get_registry
    registry = get_registry()
    def reporthook(n, row):
        _print_status(n, _('Downloading %s...') % row[DESCRIPTION_BIG])
    for row, f in ul.download_selected_updates(reporthook):
        if f is None: continue # cancelled or network error
        try:
            _print_status(None, _('Examining %s...') % row[DESCRIPTION_BIG])
            b = actutils.BundleHelper(f)
            if b.is_installed(registry):
                _print_status(None, _('Upgrading %s...') % row[DESCRIPTION_BIG])
            else:
                _print_status(None, _('Installing %s...')% row[DESCRIPTION_BIG])
            b.install_or_upgrade(registry)
        except:
            print
            print _('Error installing %s.') % row[DESCRIPTION_BIG]
            import traceback
            traceback.print_exc() # complain!  but go on.
        if os.path.exists(f):
            os.unlink(f)
        else:
            print "Failed trying to clean up", f

# 'update_groups' key -----------------------------
def get_update_groups():
    # only here to get this key to show up for '-l'
    raise ValueError('This method not used by the GUI')

def print_update_groups():
    group_urls = actinfo.get_activity_group_urls()
    for gurl in group_urls:
        # we could do a network operation to get a prettier name for
        # the group here, but let's keep the cmd-line interface simple.
        print gurl

def set_update_groups(groups):
    """
    Set URLs for update groups used by the software update control panel.
    Activities referenced by the given URLs will be suggested for installation
    on this laptop, if they are not already present.  This allows deployments
    to update the set of activities installed, as well as the actual activities.

    The parameter should be a white-space separated list of group URLs.
    """
    with open(actinfo.USER_GROUPS_FILE, 'w') as f:
        for gurl in groups.split():
            print >>f, gurl

#########################################################################
# Self-test code.
def _main():
    """Self-test."""
    print_available_updates()
    #set_install_update('all')

if __name__ == '__main__': _main ()
