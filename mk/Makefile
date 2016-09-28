USE_BRANDING := yes
IMPORT_BRANDING := yes
IMPORT_VERSIONS := yes
REPONAME := xencert

include $(B_BASE)/common.mk
include $(B_BASE)/rpmbuild.mk

-include $(MY_OBJ_DIR)/version.inc
.PHONY: $(MY_OBJ_DIR)/version.inc
$(MY_OBJ_DIR)/version.inc:
	$(version-makefile) > $@
	$(call git_cset_number,$(REPONAME)) >> $@
	echo XENCERT_VERSION := \$$\(PLATFORM_VERSION\) >> $@
	echo XENCERT_RELEASE := xs\$$\(CSET_NUMBER\) >> $@

XENCERT_SPEC := xencert.spec
XENCERT_SRC := $(RPM_SOURCESDIR)/xencert-$(XENCERT_VERSION).tar.gz
XENCERT_SRPM := xencert-$(XENCERT_VERSION)-$(XENCERT_RELEASE).src.rpm
XENCERT_STAMP := $(MY_OBJ_DIR)/.rpmbuild.stamp
REPO := $(call git_loc,xencert)

# Definition of the pack.
PACK_LABEL := xencert
PACK_VERSION := $(XENCERT_VERSION)
PACK_UUID := 38139de0-ef99-460b-ba8f-b86fca52c2e9
PACK_DESCRIPTION := The XenServer Storage Certification Kit

# Contents of the pack.
PACK_PACKAGES = $(RPM_RPMSDIR)/$(DOMAIN0_ARCH_OPTIMIZED)/xencert-$(XENCERT_VERSION)-$(XENCERT_RELEASE).$(DOMAIN0_ARCH_OPTIMIZED).rpm

ISO := $(MY_OUTPUT_DIR)/xencert-supp-pack.iso
SRCS := $(MY_OUTPUT_DIR)/$(PACK_LABEL)-sources.tar

BUILD_DIR := $(MY_OBJ_DIR)/$(PACK_LABEL)

BASE_REQUIRES ?= platform-version = $(PLATFORM_VERSION)

GPG_KEY_FILE := RPM-GPG-KEY-XS-Eng-Test
GPG_OPTIONS := --homedir=/.gpg --lock-never --batch --yes
GPG_UID := $(shell gpg $(GPG_OPTIONS) -k --with-colons 2>/dev/null | awk -F: '$$1=="uid" {print $$10}')

.PHONY: build
build: sources $(XENCERT_STAMP) $(ISO) $(SRCS)

$(MY_SOURCES)/MANIFEST: $(RPM_SRPMSDIR)/$(XENCERT_SRPM) $(MY_SOURCES_DIRSTAMP) $(RPM_BUILD_COOKIE)
	rm -f $@
	/bin/sh ./srpms-to-manifest $(COMPONENT) $(MY_OUTPUT_DIR)/SRPMS > $@

.PHONY: sources
sources: $(MY_SOURCES)/MANIFEST

.PHONY: clean
clean:
	rm -f $(XENCERT_STAMP) $(XENCERT_SRC) $(RPM_SPECSDIR)/$(XENCERT_SPEC)

.SECONDARY: $(XENCERT_SRC)
$(XENCERT_SRC): $(XENCERT_SOURCES)
	echo XENCERT_VERSION $(XECERT_VERSION) PLATFORM_VERSION $(PLATFORM_VERSION)
	cd ..; git archive --prefix=xencert-$(XENCERT_VERSION)/ HEAD > $(RPM_SOURCESDIR)/xencert-$(XENCERT_VERSION).tar
	bzip2 -f $(RPM_SOURCESDIR)/xencert-$(XENCERT_VERSION).tar

.SECONDARY: $(RPM_SPECSDIR)/%.spec
$(RPM_SPECSDIR)/%.spec: *.spec.in
	sed -e 's/@XENCERT_VERSION@/$(XENCERT_VERSION)/g' -e 's/@XENCERT_RELEASE@/$(XENCERT_RELEASE)/g' < $< > $@

$(RPM_SRPMSDIR)/$(XENCERT_SRPM): $(RPM_DIRECTORIES) $(RPM_SPECSDIR)/$(XENCERT_SPEC) $(XENCERT_SRC)
	$(RPMBUILD) -bs $(RPM_SPECSDIR)/$(XENCERT_SPEC)

$(XENCERT_STAMP): $(RPM_SRPMSDIR)/$(XENCERT_SRPM)
	$(RPMBUILD) --rebuild --target $(DOMAIN0_ARCH_OPTIMIZED) $(RPM_SRPMSDIR)/$(XENCERT_SRPM)
	touch $@

$(ISO): $(MY_OUTPUT_DIR)/.dirstamp $(PACK_PACKAGES)
	GNUPGHOME=/.gpg build-update --uuid $(PACK_UUID) --label "$(PACK_LABEL)" --version $(PACK_VERSION) \
		--description "$(PACK_DESCRIPTION)" --base-requires "$(BASE_REQUIRES)" $(PACK_GUIDANCE:%=--after-apply %) \
		$(PACK_PRECHECK:%=--precheck %) $(PACK_REMOVE:%=--remove %) \
		--key "$(GPG_UID)" --keyfile $(GPG_KEY_FILE) --no-passphrase \
		-o $@ $(PACK_PACKAGES)
	(cd $(@D) ; md5sum $(notdir $@) ) >$@.md5

$(SRCS): $(RPM_SRPMSDIR)/$(XENCERT_SRPM)
	tar czf $@ --owner=root --group=root -C $(RPM_SRPMSDIR) $(XENCERT_SRPM)
