#
# OpenWrt-пакет nodes-tester: nodes_fetch + nodes_config + nodes_tester + веб-админка +
# роутерные скрипты. Чистый Python — один пакет на все архитектуры (PKGARCH:=all).
#
# Исходник — сам репозиторий (два уровня выше этого Makefile), поэтому фид подключается
# как src-link на каталог openwrt/ репозитория (см. openwrt/README.md).
#

include $(TOPDIR)/rules.mk

PKG_NAME:=nodes-tester
PKG_VERSION:=0.3.12
PKG_RELEASE:=1

PKG_MAINTAINER:=Andrey Dyadyk <andrey.dyadyk@protonmail.ch>
PKG_LICENSE:=0BSD

PYSOCKS_VERSION:=1.7.1
PYSOCKS_FILE:=PySocks-$(PYSOCKS_VERSION).tar.gz

include $(INCLUDE_DIR)/package.mk

REPO_ROOT:=$(CURDIR)/../..
APP_DIR:=/usr/lib/nodes-tester
PY_PACKAGES:=naming nodes_common nodes_fetch nodes_config nodes_tester dashboard nodes_admin
ROUTER_SCRIPTS:=pipeline.sh pipeline-lock.sh apply-nodes.sh update-rules.sh build-clients.sh merge-configs.py \
	backup-nodes-tester.sh rollback-nodes-tester.sh

define Package/nodes-tester
  SECTION:=net
  CATEGORY:=Network
  TITLE:=sing-box node manager: fetch, config, tester, web admin
  URL:=https://github.com/ancored/nodes-tester
  PKGARCH:=all
  # Зависимости — только в метаданных пакета (EXTRA_DEPENDS): apk ставит их из фидов
  # OpenWrt, а SDK не пересобирает их из исходников (python3-jsonschema тянет rust).
  EXTRA_DEPENDS:=python3-light (>=3.8), python3-sqlite3 (>=3.8), python3-urllib (>=3.8), \
	python3-email (>=3.8), python3-openssl (>=3.8), python3-requests (>=2.31), \
	python3-yaml (>=6.0), python3-ruamel-yaml (>=0.17), python3-jsonschema (>=4.0)
endef

define Package/nodes-tester/description
  Manages sing-box proxy nodes on the router: downloads provider subscriptions
  (nodes_fetch), builds outbounds and region groups (nodes_config), continuously
  tests every node through the running sing-box and switches production selectors
  (nodes_tester), web admin (dashboard) and the cron pipeline scripts.
endef

define Package/nodes-tester/conffiles
/etc/config/nodes-tester
/etc/nodes-tester/config.json
/etc/nodes-tester/clients.list
/etc/nodes-tester/config-main/
/etc/nodes-tester/config-wh/
/etc/nodes-tester/singbox/
/etc/nodes-tester/pipeline.json
endef

# PySocks нет в фидах OpenWrt: берём два модуля из sdist с PyPI (BSD-3-Clause)
define Download/pysocks
  URL:=https://files.pythonhosted.org/packages/source/P/PySocks
  FILE:=$(PYSOCKS_FILE)
  HASH:=3f8804571ebe159c380ac6de37643bb4685970655d3bba243530d6558b799aa0
endef
$(eval $(call Download,pysocks))

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(foreach p,$(PY_PACKAGES),cp -a $(REPO_ROOT)/$(p) $(PKG_BUILD_DIR)/;)
	mkdir -p $(PKG_BUILD_DIR)/scripts/router
	$(foreach s,$(ROUTER_SCRIPTS),cp -a $(REPO_ROOT)/scripts/router/$(s) $(PKG_BUILD_DIR)/scripts/router/;)
	# только шаблоны и схема: рабочие конфиги с секретами в config/ в пакет не попадают
	mkdir -p $(PKG_BUILD_DIR)/config/awg
	$(foreach f,config.example.json config.schema.json providers.example.json \
		user_nodes.example.json groups_params.json awg/de.conf.example,\
		cp $(REPO_ROOT)/config/$(f) $(PKG_BUILD_DIR)/config/$(f);)
	cp $(REPO_ROOT)/scripts/router/clients.list.example $(PKG_BUILD_DIR)/config/
	mkdir -p $(PKG_BUILD_DIR)/config/singbox
	cp $(REPO_ROOT)/config/singbox/rules.example.json $(PKG_BUILD_DIR)/config/singbox/
	mkdir -p $(PKG_BUILD_DIR)/config/singbox/presets
	cp $(REPO_ROOT)/config/singbox/presets/*.json $(PKG_BUILD_DIR)/config/singbox/presets/
	cp $(REPO_ROOT)/config/pipeline.example.json $(PKG_BUILD_DIR)/config/
	mkdir -p $(PKG_BUILD_DIR)/schemas
	cp $(REPO_ROOT)/schemas/pipeline.schema.json $(REPO_ROOT)/schemas/groups_params.schema.json \
		$(PKG_BUILD_DIR)/schemas/
	find $(PKG_BUILD_DIR) -name __pycache__ -type d -prune -exec rm -rf {} +
	$(TAR) -xzf $(DL_DIR)/$(PYSOCKS_FILE) -C $(PKG_BUILD_DIR) \
		PySocks-$(PYSOCKS_VERSION)/socks.py PySocks-$(PYSOCKS_VERSION)/sockshandler.py \
		PySocks-$(PYSOCKS_VERSION)/LICENSE
endef

define Build/Compile
endef

define Package/nodes-tester/install
	$(INSTALL_DIR) $(1)$(APP_DIR)/scripts/router $(1)$(APP_DIR)/schemas
	# админка проверяет groups_params по схеме из каталога кода
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/schemas/groups_params.schema.json $(1)$(APP_DIR)/schemas/
	$(foreach p,$(PY_PACKAGES),$(CP) $(PKG_BUILD_DIR)/$(p) $(1)$(APP_DIR)/;)
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/PySocks-$(PYSOCKS_VERSION)/socks.py $(1)$(APP_DIR)/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/PySocks-$(PYSOCKS_VERSION)/sockshandler.py $(1)$(APP_DIR)/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/PySocks-$(PYSOCKS_VERSION)/LICENSE $(1)$(APP_DIR)/LICENSE.PySocks
	$(foreach s,$(ROUTER_SCRIPTS),$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/router/$(s) $(1)$(APP_DIR)/scripts/router/;)

	# Шаблоны: из них uci-defaults создаёт рабочие конфиги при первой установке
	$(INSTALL_DIR) $(1)/usr/share/nodes-tester/examples/awg $(1)/usr/share/nodes-tester/examples/singbox/presets
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/config.example.json $(1)/usr/share/nodes-tester/examples/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/providers.example.json $(1)/usr/share/nodes-tester/examples/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/user_nodes.example.json $(1)/usr/share/nodes-tester/examples/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/groups_params.json $(1)/usr/share/nodes-tester/examples/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/clients.list.example $(1)/usr/share/nodes-tester/examples/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/singbox/rules.example.json $(1)/usr/share/nodes-tester/examples/singbox/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/singbox/presets/*.json $(1)/usr/share/nodes-tester/examples/singbox/presets/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/pipeline.example.json $(1)/usr/share/nodes-tester/examples/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/awg/de.conf.example $(1)/usr/share/nodes-tester/examples/awg/

	# Схема принадлежит пакету (обновляется вместе с кодом), лежит рядом с config.json
	$(INSTALL_DIR) $(1)/etc/nodes-tester
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/config.schema.json $(1)/etc/nodes-tester/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/schemas/pipeline.schema.json $(1)/etc/nodes-tester/

	$(INSTALL_DIR) $(1)/etc/config $(1)/etc/init.d $(1)/etc/uci-defaults $(1)/usr/bin
	$(INSTALL_CONF) ./files/nodes-tester.config $(1)/etc/config/nodes-tester
	$(INSTALL_BIN) ./files/nodes-tester.init $(1)/etc/init.d/nodes-tester
	$(INSTALL_BIN) ./files/nodes-tester.uci-defaults $(1)/etc/uci-defaults/90-nodes-tester
	$(INSTALL_BIN) ./files/nodes-tester.sh $(1)/usr/bin/nodes-tester
endef

# __pycache__ создаёт Python во время работы — пакетный менеджер о нём не знает
define Package/nodes-tester/postrm
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
find /usr/lib/nodes-tester -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
rmdir -p /usr/lib/nodes-tester/scripts/router 2>/dev/null
exit 0
endef

$(eval $(call BuildPackage,nodes-tester))
