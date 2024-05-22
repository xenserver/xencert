DESTDIR ?= ./opt/xensource/debug/XenCert

INSTALL_DATA := install -D -m644
INSTALL_BIN := install -D -m755

# ==============================================================================

# Julian Chesterfield, July 2007
#
# Copyright (c) 2007 XenSource, Inc. All use and distribution of this
# copyrighted material is governed by and subject to terms and
# conditions as licensed by XenSource, Inc. All other rights reserved.

DDTDIR := src/diskdatatest
DDT_BIN := diskdatatest

DDT_FILES := $(wildcard $(DDTDIR)/*.c)

DDT_BUILD_OPTS := \
	-D_GNU_SOURCE \
	-D_FILE_OFFSET_BITS=64 \
	-D_LARGEFILE_SOURCE \
	-D_LARGEFILE64_SOURCE \
	-g

$(DDTDIR)/$(DDT_BIN): $(DDT_FILES)
	$(CC) $(CCOPTS) $(DDT_BUILD_OPTS) -o $@ $^

$(DESTDIR)/$(DDT_BIN): $(DDTDIR)/$(DDT_BIN)
	$(INSTALL_BIN) $< $@

# ==============================================================================

XENCERTDIR := src/XenCert

$(DESTDIR)/%: $(XENCERTDIR)/%
	$(if $(findstring #!,$(shell head -n1 $<)), \
		$(INSTALL_BIN) $< $@, \
		$(INSTALL_DATA) $< $@ \
	)

# ==============================================================================

TARGET_FILES := \
	$(notdir $(wildcard $(XENCERTDIR)/*)) \
	$(DDT_BIN) \

.PHONY: build
build: $(DDTDIR)/$(DDT_BIN)

.PHONY: install
install: $(TARGET_FILES:%=$(DESTDIR)/%)
