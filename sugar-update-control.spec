%define python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")

Summary: Activity update control panel for Sugar
Name:    sugar-update-control
Version: 0.27
Release: 1%{?dist}
License: GPLv2+
Group:   System Environment/Base
URL:     http://git.sugarlabs.org/projects/sugar-update-control
Source0: http://download.sugarlabs.org/sources/honey/sugar-update-control/%{name}-%{version}.tar.gz
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)
Requires: sugar >= 0.95.0, bitfrost-sugar
BuildRequires: gettext
BuildRequires: intltool
BuildRequires: python-devel 
BuildRequires: python-distutils-extra
BuildArch: noarch

%description
This package contains a control panel for the Sugar graphical environment
which locates and installs activity updates.

%prep
%setup -q

%build
%{__python} setup.py build

%install
rm -rf $RPM_BUILD_ROOT
mkdir -p $RPM_BUILD_ROOT
%{__python} setup.py install --root=$RPM_BUILD_ROOT

%find_lang %{name}

%clean
rm -rf $RPM_BUILD_ROOT

%files -f %{name}.lang
%defattr(-,root,root,-)
%doc README COPYING
%{python_sitelib}/*
%{_datadir}/sugar/extensions/cpsection/*

%changelog
* Tue May 15 2012 Daniel Drake <dsd@laptop.org> 0.24-2
- Add dep on bitfrost-sugar

* Mon May 30 2011 Daniel Drake <dsd@laptop.org> 0.24-1
- New release

* Tue Dec  8 2009 Daniel Drake <dsd@laptop.org> 0.23-1
- New release

* Sun Jul 26 2009 Fedora Release Engineering <rel-eng@lists.fedoraproject.org> - 0.21-2
- Rebuilt for https://fedoraproject.org/wiki/Fedora_12_Mass_Rebuild

* Fri Jul 10 2009 Daniel Drake <dsd@laptop.org> 0.21-1
- Update to v0.21

* Tue Jun 9 2009 Steven M. Parrish <tuxbrewr@fedoraproject.org> 0.20-5
- Fix missing bitfrost

* Sun Mar 08 2009 <tuxbrewr@fedoraproject.org> 0.20-4
- Fix missing deps	

* Sat Mar 07 2009 <bernie@codewiz.org> - 0.20-3
- Merge with tuxbrewr spec

* Thu Mar 05 2009 Steven M. Parrish <tuxbrewr@fedoraproject.org> 0.20-2
- Fix license and files

* Wed Mar 04 2009 Steven M. Parrish <tuxbrewr@fedoraproject.org> 0.20-1
- New upstream release

* Fri Jan 23 2009 Steven M. Parrish <tuxbrewr@fedoraproject.org> 0.19-2
- Initial build for Fedora repos

* Wed Dec 17 2008 C. Scott Ananian <cscott@laptop.org>
- Fix packaging problems; actually distribute translation files.

* Fri Dec 12 2008 Chris Ball <cjb@laptop.org>
- Trac #9044: Pick up Spanish translations for release.

* Sun Sep 28 2008 C. Scott Ananian <cscott@laptop.org>
- Trac #7845, #8681: don't die if ~/Activities doesn't exist.
- This fixes a regression introduced in 0.15.

* Thu Sep 25 2008 C. Scott Ananian <cscott@laptop.org>
- Filter out library/library.info when it is not in the root directory
  of the bundle.

* Wed Sep 24 2008 C. Scott Ananian <cscott@laptop.org>
- Minor fixes: strip whitespace when parsing HTML, avoid an unusual crash
  when cleaning up from a failed install.
- If we have applied the 'G1G1 upgrade hack' to kludge in the G1G1 group,
  make sure this extra group persists.  This avoids incomplete installs
  if a network error occurs in the middle.

* Wed Sep 17 2008 C. Scott Ananian <cscott@laptop.org>
- Trac #8502: clean up icon cruft in /tmp
- Trac #8106: support content bundles in addition to activity bundles.
- Trac #8532: disable SIGCHLD handler to fix thread & subprocess interaction.
- Properly time out slow activity group fetches.

* Tue Sep 16 2008 C. Scott Ananian <cscott@laptop.org>
- Trac #8415: set base HREF for redirected update_urls correctly.
- Trac #8502: clean up URL cache when control panel is closed.

* Fri Sep 12 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream: 0.12
- Trac #8361, #8393: don't allow bad update_urls to crash the updater.
- Disable auto-close.

* Wed Sep 02 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream: 0.11
- Fix egregious typos in v0.10.

* Tue Sep 02 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream: 0.10
- Trac #8149: don't let malformed activity bundles kill the updater.
- Allow groups to provide newer versions of the activities then exist
  at the activities update_url, to make it easier to create a group
  for testing 'prerelease' versions.

* Wed Aug 20 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream: 0.9
- Trac #7979: fix downloads via squid-using school server.
  Workaround for python issue 3566: http://bugs.python.org/issue3566
- Improve support for cancelling incomplete, hung, or slow downloads.
- Trac #7865: stale "download size" label contents.
- Don't use partial reads unnecessarily to download activity bundles.
- Better installation feedback for delays caused by trac #7733.

* Wed Aug 13 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream: 0.8
- Catch 404 when trying to determine the size of a prospective update.
- Catch exception caused by malformed activity bundle.
- Use update_url in bundle's activity.info if present.
- Inhibit suspend during refresh and download/install.
- Trac #7622: preserve favorites status across upgrade.

* Tue Aug 07 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream: 0.7.

* Wed Aug 06 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream: 0.6.
- Update to match upstream patch for dlo trac #7495.
- Don't download large activity bundles to memory.

* Thu Jul 31 2008 C. Scott Ananian <cscott@laptop.org>
- New upstream, with UI improvements.

* Fri Jul 11 2008 C. Scott Ananian <cscott@laptop.org>
- Initial packaging.
