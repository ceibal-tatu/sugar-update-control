#!/usr/bin/python
# Copyright (C) 2008 One Laptop Per Child Association, Inc.
# Licensed under the terms of the GNU GPL v2 or later; see COPYING for details.
# Written by C. Scott Ananian <cscott@laptop.org>
"""Activity updater.

Checks for updates to activities and installs them."""
from __future__ import with_statement
from __future__ import division
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import GObject
GLib.threads_init()

import logging
import gettext
import os
import re
from threading import Thread, Event
import gettext

_ = lambda msg: gettext.dgettext('sugar-update-control', msg)

import bitfrost.update.actutils as actutils
from sugar3.graphics import style

from jarabe.controlpanel.sectionview import SectionView
from jarabe.controlpanel.inlinealert import InlineAlert
from jarabe.model import bundleregistry
from jarabe.view.buddymenu import get_control_panel

import model
from model import _humanize_size, _svg2pixbuf, inhibit_suspend

# configuration constants needed for control panel framework
CLASS = 'ActivityUpdater'
ICON = 'module-updater'
TITLE = _('Software update')

# for debugging
_DEBUG_VIEW_ALL=False
"""View even activities with no pending updates."""

_e = GObject.markup_escape_text
"""Useful abbreviation."""

def _make_button(label_text, stock=None, name=None):
    """Convenience function to make labelled buttons with images."""
    b = Gtk.Button()
    hbox = Gtk.HBox()
    hbox.set_spacing(style.DEFAULT_PADDING)
    i = Gtk.Image()
    if stock is not None:
        i.set_from_stock(stock, Gtk.IconSize.BUTTON)
    if name is not None:
        i.set_from_icon_name(name, Gtk.IconSize.BUTTON)
    hbox.pack_start(i, False, True, 0)
    l = Gtk.Label(label=label_text)
    hbox.pack_start(l, False, True, 0)
    b.add(hbox)
    return b

### Pieces of the activity updater view; factored to make the UI structure
### more apparent in `ActivityUpdater.__init__`.

class ActivityListView(Gtk.ScrolledWindow):
    """List view at the top, showing activities, versions, and sizes."""
    def __init__(self, activity_updater, activity_pane):
        GObject.GObject.__init__(self)
        self.activity_updater = activity_updater
        self.activity_pane = activity_pane

        # create the TreeView using a filtered treestore
        self.ftreestore = self.activity_updater.activity_list.filter_new()
        if not _DEBUG_VIEW_ALL:
            self.ftreestore.set_visible_column(model.UPDATE_EXISTS)
        self.treeview = Gtk.TreeView(self.ftreestore)
   
        # create some cell renderers.
        crbool = Gtk.CellRendererToggle()
        crbool.set_property('activatable', True)
        crbool.set_property('xpad', style.DEFAULT_PADDING)
        # indicator size should be themeable, but is not.
        # if we're in sugar, use the larger indicator size.
        # otherwise, use the hard-coded GTK default.
        if self.activity_updater._in_sugar:
            crbool.set_property('indicator_size', style.zoom(26))
        def toggled_cb(crbool, path, self):
            path = Gtk.TreePath(path)
            path = self.ftreestore.convert_path_to_child_path(path)
            self.activity_updater.activity_list.toggle_select(path)
            self.activity_pane._refresh_update_size()
        crbool.connect('toggled', toggled_cb, self)

        cricon = Gtk.CellRendererPixbuf()
        cricon.set_property('width', style.STANDARD_ICON_SIZE)
        cricon.set_property('height', style.STANDARD_ICON_SIZE)

        crtext = Gtk.CellRendererText()
        crtext.set_property('xpad', style.DEFAULT_PADDING)
        crtext.set_property('ypad', style.DEFAULT_PADDING)
        
        # create the TreeViewColumn to display the data
        def view_func_maker(propname):
            def view_func(cell_layout, renderer, m, it, user_data=None):
                renderer.set_property(propname,
                                      not m.get_value(it, model.IS_HEADER))
            return view_func
        hide_func = view_func_maker('visible')
        insens_func = view_func_maker('sensitive')
        self.column_install = Gtk.TreeViewColumn('Install', crbool)
        self.column_install.add_attribute(crbool, 'active', model.UPDATE_SELECTED)
        self.column_install.set_cell_data_func(crbool, hide_func)
        self.column = Gtk.TreeViewColumn('Name')
        self.column.pack_start(cricon, False)
        self.column.pack_start(crtext, True)
        self.column.add_attribute(cricon, 'pixbuf', model.ACTIVITY_ICON)
        self.column.set_resizable(True)
        self.column.set_cell_data_func(cricon, hide_func)
        def markup_func(cell_layout, renderer, m, it, user_data):
            s = '<b>%s</b>' % _e(m.get_value(it, model.DESCRIPTION_BIG))
            if m.get_value(it, model.IS_HEADER):
                s = '<big>%s</big>' % s
            desc = m.get_value(it, model.DESCRIPTION_SMALL)
            if desc is not None and desc != '':
                s += '\n<small>%s</small>' % _e(desc)
            renderer.set_property('markup', s)
            insens_func(cell_layout, renderer, m, it)
        self.column.set_cell_data_func(crtext, markup_func)
   
        # add tvcolumn to treeview
        self.treeview.append_column(self.column_install)
        self.treeview.append_column(self.column)

        self.treeview.set_reorderable(False)
        self.treeview.set_enable_search(False)
        self.treeview.set_headers_visible(False)
        self.treeview.set_rules_hint(True)
        self.treeview.connect('button-press-event', self.show_context_menu)

        def is_valid_cb(activity_list, __):
            self.treeview.set_sensitive(activity_list.is_valid())
        self.activity_updater.activity_list.connect('notify::is-valid',
                                                    is_valid_cb)
        is_valid_cb(self.activity_updater.activity_list, None)

        # now put this all inside ourself (a Gtk.ScrolledWindow)
        self.add_with_viewport(self.treeview)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

    def unlink_model(self):
        self.treeview.set_model(None)
    def relink_model(self):
        self.ftreestore.refilter()
        self.treeview.set_model(self.ftreestore)

    def show_context_menu(self, widget, event):
        """
        Show a context menu if a right click was performed on an update entry
        """
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            activity_list = self.activity_updater.activity_list
            def cb(__, f):
                f()
                self.activity_pane._refresh_update_size()
            menu = Gtk.Menu()
            item_select_none = Gtk.MenuItem(_("_Uncheck All"))
            item_select_none.connect("activate", cb,
                                     activity_list.unselect_all)
            menu.add(item_select_none)
            if activity_list.updates_available() == 0:
                item_select_none.set_property("sensitive", False)
            item_select_all = Gtk.MenuItem(_("_Check All"))
            item_select_all.connect("activate", cb,
                                    activity_list.select_all)
            menu.add(item_select_all)
            menu.popup(None, None, None, 0, event.time)
            menu.show_all()
            return True
        return False

class GroupListView(Gtk.VBox):
    """List view in expander at bottom, showing groups and urls."""
    def __init__(self, activity_updater):
        GObject.GObject.__init__(self)
        self.set_spacing(style.DEFAULT_PADDING)
        self.activity_updater = activity_updater

        self.fgroupstore = self.activity_updater.activity_list.filter_new()
        def group_visibility(m, it, user_data=None):
            # only group header rows, but not the 'local activities' group.
            return m.get_value(it, model.IS_HEADER) and \
                   m.get_value(it, model.UPDATE_URL) is not None
        self.fgroupstore.set_visible_func(group_visibility)
        self.groupview = Gtk.TreeView(self.fgroupstore)
        self.groupview.set_headers_visible(False)
        self.groupview.set_rules_hint(True)
        self.groupview.set_enable_search(False)
        self.groupview.set_reorderable(False) # we'll write own DnD funcs below
        # FIXME: port to GTK3
        #TARGETS = Gtk.TargetList()
        #TARGETS.add_text_targets(0)
        #TARGETS.add_uri_targets(1)
        #self.groupview.enable_model_drag_source( Gdk.ModifierType.BUTTON1_MASK,
        #                                         TARGETS,
        #                                         Gdk.DragAction.DEFAULT
        #                                         |Gdk.DragAction.COPY
        #                                         |Gdk.DragAction.MOVE)
        #self.groupview.enable_model_drag_dest(TARGETS,
        #                                      Gdk.DragAction.DEFAULT
        #                                      |Gdk.DragAction.COPY
        #                                      |Gdk.DragAction.MOVE)
        def drag_data_get_data(groupview, context, drag_selection, target_id, etime):
            selection = groupview.get_selection()
            m, it = selection.get_selected()
            gurl = m.get_value(it, model.UPDATE_URL)
            drag_selection.set_text(gurl)
            drag_selection.set_uris([gurl])
        # for info on our DnD strategy here, see
        # http://lists-archives.org/gtk/06954-treemodelfilter-drag-and-drop.html
        def drag_drop(groupview, context, x, y, time, data=None):
            targets = groupview.drag_dest_get_target_list()
            t = groupview.drag_dest_find_target(context, targets)
            drag_selection = groupview.drag_get_data(context, t)
            return True # don't invoke parent.
        def drag_data_received_data(groupview, context, x, y, drag_selection,
                                    info, etime):
            # weird gtk workaround; see mailing list post referenced above.
            groupview.emit_stop_by_name('drag_data_received')
            # ok, now see if this is a move from the same widget
            same_widget = (context.get_source_widget() == groupview)
            m = groupview.get_model()
            gurl = drag_selection.data
            drop_info = groupview.get_dest_row_at_pos(x, y)
            if drop_info is None:
                desired_group_num = -1 # at end.
            else:
                path, position = drop_info
                path = self.fgroupstore.convert_path_to_child_path(path)
                desired_group_num = self.activity_updater\
                                    .activity_list[path][model.GROUP_NUM]
                if position == Gtk.TreeViewDropPosition.AFTER \
                   or position == Gtk.TreeViewDropPosition.INTO_OR_AFTER:
                    desired_group_num += 1
            if not same_widget:
                self.activity_updater.activity_list.add_group(gurl)
            success = self.activity_updater.activity_list\
                      .move_group(gurl, desired_group_num)
            want_delete = (context.action == Gdk.DragAction.MOVE) and \
                          not same_widget # we'll handle the delete ourselves
            context.finish(success, want_delete and success, etime)
            return True # don't invoke parent.
        self.groupview.connect("drag_data_get", drag_data_get_data)
        self.groupview.connect("drag_data_received", drag_data_received_data)
        self.groupview.connect("drag_drop", drag_drop)
        crtext = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn('Name', crtext)
        def group_name_markup(cell_layout, renderer, m, it, user_data):
            renderer.set_property('markup', '<b>%s</b>\n<small>%s</small>' % \
                                  (_e(m.get_value(it, model.DESCRIPTION_BIG)),
                                   _e(m.get_value(it, model.UPDATE_URL))))
        column.set_cell_data_func(crtext, group_name_markup)
        self.groupview.append_column(column)
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.add_with_viewport(self.groupview)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        hbox2 = Gtk.HBox()
        hbox2.set_spacing(style.DEFAULT_PADDING)
        label = Gtk.Label(label=_('Group URL:'))
        hbox2.pack_start(label, False, True, 0)
        self.group_entry = Gtk.Entry()
        hbox2.pack_start(self.group_entry, True, True, 0)
        self.group_add_button = Gtk.Button(stock=Gtk.STOCK_ADD)
        self.group_del_button = Gtk.Button(stock=Gtk.STOCK_REMOVE)
        self.group_del_button.set_sensitive(False)
        hbox2.pack_start(self.group_add_button, False, True, 0)
        hbox2.pack_start(self.group_del_button, False, True, 0)

        selection = self.groupview.get_selection()
        selection.set_mode(Gtk.SelectionMode.SINGLE)
        def group_select_cb(selection):
            (m, it) = selection.get_selected()
            if it is None: return
            self.group_entry.set_text(m.get_value(it, model.UPDATE_URL))
        def group_entry_changed_cb(group_entry):
            selection = self.groupview.get_selection()
            (m, it) = selection.get_selected()
            if it is not None and \
               group_entry.get_text() == m.get_value(it, model.UPDATE_URL):
                is_add = False
            else:
                is_add = True
                selection.unselect_all()
            self.group_add_button.set_sensitive(is_add)
            self.group_del_button.set_sensitive(not is_add)
        selection.connect('changed', group_select_cb)
        self.group_entry.connect('changed', group_entry_changed_cb)
        self.group_entry.connect('activate', self._add_group_cb, self)
        self.group_add_button.connect('clicked', self._add_group_cb, self)
        self.group_del_button.connect('clicked', self._del_group_cb, self)

        self.pack_start(scrolled_window, True, True, 0)
        self.pack_start(hbox2, False, True, 0)

    def _add_group_cb(self, widget, event, data=None):
        success = self.activity_updater.activity_list\
                  .add_group(self.group_entry.get_text())
        if success:
            self.group_entry.set_text('')
            self.groupview.scroll_to_cell(self.fgroupstore[-1].path)

    def _del_group_cb(self, widget, event, data=None):
        success = self.activity_updater.activity_list\
                  .del_group(self.group_entry.get_text())
        if success:
            self.group_entry.set_text('')

    def unlink_model(self):
        self.groupview.set_model(None)

    def relink_model(self):
        self.fgroupstore.refilter()
        self.groupview.set_model(self.fgroupstore)

class ActivityPane(Gtk.VBox):
    """Container for the activity and group lists."""

    def __init__(self, activity_updater):
        GObject.GObject.__init__(self)
        self.activity_updater = activity_updater
        self.set_spacing(style.DEFAULT_PADDING)

        ## activity list at top
        vpaned = Gtk.VPaned()
        self.activities = ActivityListView(activity_updater, self)
        vpaned.pack1(self.activities, resize=True, shrink=False)

        ## expander/group list view at bottom
        self.expander = Gtk.Expander(label=_('Modify activity groups'))
        self.expander.set_use_markup(True)
        def expander_callback(expander, __):
            if not expander.get_expanded(): # when groups are collapsed...
                vpaned.set_position(-1) # ...unset VPaned thumb
        self.expander.connect('notify::expanded', expander_callback)
        self.groups = GroupListView(activity_updater)
        self.expander.add(self.groups)
        vpaned.pack2(self.expander, resize=False, shrink=False)
        self.pack_start(vpaned, True, True, 0)

        ### Install/refresh buttons below these.
        button_box = Gtk.HBox()
        button_box.set_spacing(style.DEFAULT_SPACING)
        hbox = Gtk.HBox()
        hbox.pack_end(button_box, False, True, 0)
        self.size_label = Gtk.Label()
        self.size_label.set_property('xalign', 0)
        self.size_label.set_justify(Gtk.Justification.LEFT)
        hbox.pack_start(self.size_label, True, True, 0)
        self.pack_end(hbox, False, True, 0)
        self.check_button = Gtk.Button(stock=Gtk.STOCK_REFRESH)
        self.check_button.connect('clicked', activity_updater.refresh_cb, self)
        button_box.pack_start(self.check_button, False, True, 0)
        self.install_button = _make_button(_("Install selected"),
                                          name='emblem-downloads')
        self.install_button.connect('clicked', activity_updater.download_cb, self)
        button_box.pack_start(self.install_button, False, True, 0)
        def is_valid_cb(activity_list, __):
            self.install_button.set_sensitive(activity_list.is_valid())
        activity_updater.activity_list.connect('notify::is-valid', is_valid_cb)

    def unlink_models(self):
        self.activities.unlink_model()
        self.groups.unlink_model()

    def relink_models(self):
        self.activities.relink_model()
        self.groups.relink_model()

    def _refresh_update_size(self):
        """Update the 'download size' label."""
        activity_list = self.activity_updater.activity_list
        size = activity_list.updates_size()
        self.size_label.set_markup(_('Download size: %s') %
                                   _humanize_size(size))
        self.install_button.set_sensitive(activity_list.updates_selected()!=0)

    def switch(self):
        """Make the activity list visible and the progress pane invisible."""
        for widget, v in [ (self, True),
                           (self.activity_updater.progress_pane, False),
                           (self.activity_updater.expander, False)]:
            widget.set_property('visible', v)

class ProgressPane(Gtk.VBox):
    """Container which replaces the `ActivityPane` during refresh or
    install."""

    def __init__(self, activity_updater):
        self.activity_updater = activity_updater
        GObject.GObject.__init__(self)
        self.set_spacing(style.DEFAULT_PADDING)
        self.set_border_width(style.DEFAULT_SPACING * 2)

        self.bar = Gtk.ProgressBar()
        self.label = Gtk.Label()
        self.label.set_line_wrap(True)
        self.label.set_property('xalign', 0.5) # center the label.
        self.label.modify_fg(Gtk.StateType.NORMAL,
                             style.COLOR_BUTTON_GREY.get_gdk_color())
        self.icon = Gtk.Image()
        self.icon.set_property('height-request', style.STANDARD_ICON_SIZE)
        # make an HBox to center the various buttons.
        hbox = Gtk.HBox()
        self.cancel_button = Gtk.Button(stock=Gtk.STOCK_CANCEL)
        self.refresh_button = Gtk.Button(stock=Gtk.STOCK_REFRESH)
        self.try_again_button = _make_button(_('Try again'),
                                             stock=Gtk.STOCK_REFRESH)
        for widget,cb in [(self.cancel_button, activity_updater.cancel_cb),
                          (self.refresh_button, activity_updater.refresh_cb),
                          (self.try_again_button, activity_updater.refresh_cb)]:
            widget.connect('clicked', cb, activity_updater)
            hbox.pack_start(widget, True, False, 0)

        self.pack_start(self.icon, True, True, 0)
        self.pack_start(self.bar, True, True, 0)
        self.pack_start(self.label, True, True, 0)
        self.pack_start(hbox, True, True, 0)

    def update(self, n, extra=None, icon=None):
        """Update the status of the progress pane.  `n` should be a float
        in [0, 1], or else None.  You can optionally provide extra information
        in `extra` or an icon in `icon`."""
        if n is None:
            self.bar.pulse()
        else:
            self.bar.set_fraction(n)
        extra = _e(extra) if extra is not None else ''
        if False and n is not None: # XXX 'percentage' disabled; it looks bad.
            if len(extra) > 0: extra += ' '
            extra += '%.0f%%' % (n*100)
        self.label.set_markup(extra)
        self.icon.set_property('visible', icon is not None)
        if icon is not None:
            self.icon.set_from_pixbuf(icon)

    def cancelling(self):
        self.cancel_button.set_sensitive(False)
        self.label.set_markup(_('Cancelling...'))

    def _switch(self, show_cancel, show_bar, show_try_again=False):
        """Make the progress pane visible and the activity pane invisible."""
        self.activity_updater.activity_pane.set_property('visible', False)
        self.set_property('visible', True)
        for widget, v in [ (self.bar, show_bar),
                           (self.cancel_button, show_cancel),
                           (self.refresh_button,
                                not show_cancel and not show_try_again),
                           (self.try_again_button, show_try_again),
                           (self.activity_updater.expander, False)]:
            widget.set_property('visible', v)
        self.cancel_button.set_sensitive(True)
        self.activity_updater.expander.set_expanded(False)

    def switch_to_check_progress(self):
        self._switch(show_cancel=True, show_bar=True)
        self.label.set_markup(_('Checking for updates...'))

    def switch_to_download_progress(self):
        self._switch(show_cancel=True, show_bar=True)
        self.label.set_markup(_('Starting download...'))

    def switch_to_complete_message(self, msg, try_again=False):
        self._switch(show_cancel=False, show_bar=False,
                     show_try_again=try_again)
        self.label.set_markup(msg)
        self.activity_updater.expander.set_property('visible', True)

class ActivityUpdater(SectionView):
    """Software update control panel UI class."""

    def __init__(self, modelwrapper, alerts):
        SectionView.__init__(self)
        get_control_panel()._section_toolbar.cancel_button.hide()

        bundleregistry.get_registry().disable_directory_monitoring()

        self._in_sugar = (modelwrapper is not None)
        self.set_spacing(style.DEFAULT_SPACING)
        self.set_border_width(style.DEFAULT_SPACING * 2)

        # top labels.
        self.top_label = Gtk.Label()
        self.top_label.set_line_wrap(True)
        self.top_label.set_justify(Gtk.Justification.LEFT)
        self.top_label.set_property('xalign',0)
        self.top_label.set_markup('<big>%s</big>'%_('Checking for updates...'))
        bottom_label = Gtk.Label()
        bottom_label.set_line_wrap(True) # doesn't really work right =(
        bottom_label.set_justify(Gtk.Justification.LEFT)
        bottom_label.set_property('xalign', 0)
        bottom_label.set_markup(_('Software updates correct errors, eliminate security vulnerabilities, and provide new features.'))
        vbox2 = Gtk.VBox()
        vbox2.pack_start(self.top_label, False, True, 0)
        vbox2.pack_start(Gtk.HSeparator(), False, False, 0)
        vbox2.pack_start(bottom_label, True, True, 0)
        self.pack_start(vbox2, False, True, 0)

        # activity/group pane ####
        self.activity_list = model.UpdateList()
        self.activity_pane = ActivityPane(self)
        self.pack_start(self.activity_pane, True, True, 0)

        # progress pane ###########
        self.progress_pane = ProgressPane(self)
        self.pack_start(self.progress_pane, True, False, 0)

        # special little extension to progress pane.
        self.expander = Gtk.Expander(label=_('Modify activity groups'))
        def expander_cb(expander, param_):
            if expander.get_expanded():
                self.activity_pane.switch()
                self.activity_pane.expander.set_expanded(True)
                expander.set_expanded(False)
        self.expander.connect("notify::expanded", expander_cb)
        self.pack_end(self.expander, False, True, 0)

        # show our work!
        self.show_all()
        # and start refreshing.
        self.refresh_cb(None, None)

    def install_cb(self, registry, bundle, event):
        bundle.install_or_upgrade(registry)
        self.needs_restart = True
        event.set()
        return False

    def download_cb(self, widget, event, data=None):
        """Invoked when the 'ok' button is clicked."""
        from sugar3.bundle.activitybundle import ActivityBundle
        self.top_label.set_markup('<big>%s</big>' %
                                  _('Downloading updates...'))
        self.progress_pane.switch_to_download_progress()
        self._progress_cb(0, _('Starting download...'))
        self._cancel_func = lambda: self.activity_list.cancel_download()
        def progress_cb(n, extra=None, icon=None):
            GObject.idle_add(self._progress_cb, n, extra, icon)
        @inhibit_suspend
        def do_download():
            # get activity registry
            from jarabe.model.bundleregistry import get_registry
            registry = get_registry() # requires a dbus-registered main loop
            install_event = Event()
            # progress bar bookkeeping.
            counts = [0, self.activity_list.updates_selected(), 0]
            def p(n, extra, icon):
                if n is None:
                    progress_cb(n, extra, icon)
                else:
                    progress_cb((n+(counts[0]/counts[1]))/2, extra, icon)
                counts[2] = n # last fraction.
            def q(n, row):
                p(n, _('Downloading %s...') % row[model.DESCRIPTION_BIG],
                  row[model.ACTIVITY_ICON])
            for row, f in self.activity_list.download_selected_updates(q):
                if f is None: continue # cancelled or network error.
                try:
                    p(counts[2], _('Examining %s...')%row[model.DESCRIPTION_BIG],
                      row[model.ACTIVITY_ICON])
                    b = actutils.BundleHelper(f)
                    p(counts[2], _('Installing %s...') % b.get_name(),
                      _svg2pixbuf(b.get_icon_data()))
                    install_event.clear()
                    GLib.idle_add(self.install_cb, registry, b, install_event)
                    install_event.wait()
                except:
                    logging.exception("Failed to install bundle")
                    pass # XXX: use alert to indicate install failure.
                if os.path.exists(f):
                    os.unlink(f)
                counts[0]+=1
            # refresh when we're done.
            GObject.idle_add(self.refresh_cb, None, None, False)
        Thread(target=do_download).start()

    def cancel_cb(self, widget, event, data=None):
        """Callback when the cancel button is clicked."""
        self.progress_pane.cancelling()
        self._cancel_func()

    def refresh_cb(self, widget, event, clear_cache=True):
        """Invoked when the 'refresh' button is clicked."""
        self.top_label.set_markup('<big>%s</big>' %
                                  _('Checking for updates...'))
        self.progress_pane.switch_to_check_progress()
        self._cancel_func = lambda: self.activity_list.cancel_refresh()
        # unlink model from treeview, and perform actual refresh in another
        # thread.
        self.activity_pane.unlink_models()
        # freeze notify queue for activity_list to prevent thread problems.
        self.activity_list.freeze_notify()
        def progress_cb(n, extra=None):
            GObject.idle_add(self._progress_cb, n, extra)
        @inhibit_suspend
        def do_refresh():
            self.activity_list.refresh(progress_cb, clear_cache=clear_cache)
            GObject.idle_add(self._refresh_done_cb)
        Thread(target=do_refresh).start()
        return False

    def _progress_cb(self, n, extra=None, icon=None):
        """Invoked in main thread during a refresh operation."""
        self.progress_pane.update(n, extra, icon)
        return False

    def _refresh_done_cb(self):
        """Invoked in main thread when the refresh is complete."""
        self.activity_pane.relink_models()
        self.activity_list.thaw_notify()
        # clear the group url
        self.activity_pane.groups.group_entry.set_text('')
        # so, how did we do?
        if not self.activity_list.saw_network_success():
            header = _("Could not access the network")
            self.progress_pane.switch_to_complete_message(
                _('Could not access the network to check for updates.'),
                try_again=True)
        else:
            avail = self.activity_list.updates_available()
            if avail == 0:
                header = _("Your software is up-to-date")
                self.progress_pane.switch_to_complete_message(header)
            else:
                header = gettext.ngettext("You can install %s update",
                                          "You can install %s updates", avail) \
                                          % avail
                self.activity_pane.switch()
        self.top_label.set_markup('<big>%s</big>' % _e(header))
        self.activity_pane._refresh_update_size()
        # XXX: auto-close is disabled; I'm not convinced it helps the UI
        if False and self.auto_close and \
               self.activity_list.saw_network_success() and avail == 0:
            # okay, automatically close since no updates were found.
            self.emit('request-close')
        return False

    def delete_event(self, widget, event, data=None):
        return False # destroy me!

    def destroy(self, widget, data=None):
        Gtk.main_quit()

    def perform_cancel_actions(self):
        bundleregistry.get_registry().enable_directory_monitoring()
        get_control_panel()._section_toolbar.cancel_button.show()

    def perform_accept_actions(self):
        bundleregistry.get_registry().enable_directory_monitoring()
        get_control_panel()._section_toolbar.cancel_button.show()

    def main(self):
        """Start gtk main loop."""
        Gtk.main()

def _main():
    """Testing code; runs updater standalone."""
    window = Gtk.Window(Gtk.WindowType.TOPLEVEL)
    window.set_title(TITLE)
    window.set_size_request(425, 400)
    au = ActivityUpdater(None, None)
    au.connect('request-close', au.destroy) # auto-close == destroy.
    window.connect('delete_event', au.delete_event)
    window.connect('destroy', au.destroy)
    window.add(au)
    au.set_border_width(style.DEFAULT_SPACING) # our window is smaller here.
    window.show()
    Gtk.main()

# set PYTHONPATH to /usr/share/sugar/shell before invoking.
if __name__ == '__main__': _main ()
