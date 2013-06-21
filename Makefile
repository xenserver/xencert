DEBUG_DEST := /opt/xensource/debug/

SM_STAGING := $(DESTDIR)

.PHONY: build
	make -C diskdatatest

.PHONY: install
install: 
	mkdir -p $(SM_STAGING)
	$(call mkdir_clean,$(SM_STAGING))
	mkdir -p $(SM_STAGING)$(DEBUG_DEST)/XenCert
	$(MAKE) -C diskdatatest
	cp -rf XenCert $(SM_STAGING)$(DEBUG_DEST)/
	cp -rf diskdatatest/diskdatatest $(SM_STAGING)$(DEBUG_DEST)/XenCert/diskdatatest

.PHONY: clean
clean:
	rm -rf $(SM_STAGING)

