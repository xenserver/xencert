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
BUILD := $(BUILD_NUMBER)

.PHONY: build
build: sources $(XENCERT_STAMP)

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
	mkdir -p $(MY_MAIN_PACKAGES)
	cp $(RPM_RPMSDIR)/$(DOMAIN0_ARCH_OPTIMIZED)/xencert-*.rpm $(MY_MAIN_PACKAGES)
	touch $@

# FIXME Unsure of this
#$(SUPPORT_PACK): $(XENCERT_STAMP)
#	python setup.py --out $(dir $@) --pdn $(PRODUCT_BRAND) --pdv $(PRODUCT_VERSION) --bld $(BUILD) --spn "xencert-supp-pack" --spd "XenCert" --rxv "6.2.0" $(RPM_RPMSDIR)/$(DOMAIN0_ARCH_OPTIMIZED)/sm-xencert-*.i686.rpm
