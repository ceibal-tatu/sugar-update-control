VERSION=$(shell python setup.py -V)
PACKAGE=sugar-update-control
MOCK=./mock-wrapper -r olpc-3-i386 --resultdir=$(MOCKDIR) $(MOCK_OPTS)
MOCKDIR=./rpms
PKGVER=$(PACKAGE)-$(VERSION)
CWD=$(shell pwd)

# update the translation template
po/sugar-update-control.pot: src/__init__.py src/view.py src/model.py
	xgettext -o $@ \
	  --copyright-holder="One Laptop per Child Association, Inc." \
	  --package-name="$(PACKAGE)" \
	  --package-version="$(VERSION)" \
	  --msgid-bugs-address="cscott@laptop.org" \
	  $^

# note that this builds the tarball from *committed git bits* only.
# do a git commit before invoking this.
$(PKGVER).tar.gz:
	git diff --shortstat --exit-code # check that our working copy is clean
	git diff --cached --shortstat --exit-code # uncommitted changes?
	git archive --format=tar --prefix=$(PKGVER)/ HEAD | gzip -9 > $@
.PHONY: $(PKGVER).tar.gz # force refresh

tarball: $(PKGVER).tar.gz

# builds SRPM and RPMs in an appropriate chroot, leaving the results in
# the $(MOCKDIR) subdirectory.

# make the SRPM.
$(PKGVER)-1.src.rpm: update-version $(PKGVER).tar.gz
	rpmbuild --define "_specdir $(CWD)" --define "_sourcedir $(CWD)" --define "_builddir $(CWD)" --define "_srcrpmdir $(CWD)" --define "_rpmdir $(CWD)" --define "dist %nil" --nodeps -bs $(PACKAGE).spec

# build RPMs from the SRPM
mock:	$(PKGVER)-1.src.rpm
	@mkdir -p $(MOCKDIR)
	$(MOCK) -q --init
	$(MOCK) --installdeps $(PKGVER)-1.src.rpm
	$(MOCK) -v --no-clean --rebuild $(PKGVER)-1.src.rpm

upload:
	@mkdir -p $(MOCKDIR)
	rsync -4 -avz $(PACKAGE).changes dev.laptop.org:public_rpms/joyride
	rsync -4 -avz --include="*.rpm" --exclude="*" $(MOCKDIR)/ dev.laptop.org:public_rpms/joyride

clean:
	-$(RM) $(PKGVER)-1.src.rpm $(PKGVER).tar.gz
	-$(RM) -rf $(MOCKDIR)
