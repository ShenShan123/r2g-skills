# Deliberately no synthesis/placement dependencies. Expand with the same ORFS
# Makefile, DESIGN_CONFIG, FLOW_VARIANT and process environment as the real run.
.PHONY: r2g-effective-config
r2g-effective-config:
	@true
$(if $(filter r2g-effective-config,$(MAKECMDGOALS)),$(foreach v,$(R2G_SNAPSHOT_KEYS),$(info __R2G_CONFIG__$(v)|$(origin $(v))|$($(v)))))
