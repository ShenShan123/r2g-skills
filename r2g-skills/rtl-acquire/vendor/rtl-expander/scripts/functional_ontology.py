#!/usr/bin/env python3
"""Deterministic functional ontology for RTL corpus records."""

from __future__ import annotations

import re
from typing import Any


ONTOLOGY_SCHEMA = "rtl_function_ontology_v2"
CONFIDENCE_WEIGHTS = {"HIGH": 1.0, "MEDIUM": 0.625, "LOW": 0.125}

RULES: list[tuple[str, str]] = [
    ("pcie", r"\bpcie\b|pci[-_ ]?express"),
    ("noc", r"\bno[cs]\b|network[-_ ]on[-_ ]chip|mesh[-_ ]router"),
    ("memory_controller", r"\bddr[2-5]?\b|sdram|dram[-_ ]controller|memory[-_ ]controller"),
    ("cache", r"\bcache\b|icache|dcache|l[123][-_ ]cache"),
    ("cpu", r"\bcpu\b|risc[-_ ]?v|riscv|mips|openrisc|processor|microcontroller|\bcore[0-9]*\b"),
    ("soc", r"\bsoc\b|system[-_ ]on[-_ ]chip|chip[-_ ]top|subsystem"),
    ("accelerator", r"accelerator|\bnpu\b|\bgpu\b|tensor|systolic|matrix[-_ ]mult|neural|inference"),
    ("crypto", r"\baes\b|\bsha[0-9]*\b|crypto|cipher|chacha|poly1305|\brsa\b|ecc|keccak|kyber|dilithium|hqc"),
    ("video_image", r"video|image|camera|hdmi|vga|display|pixel|framebuffer|jpeg|h26[45]"),
    ("codec", r"codec|encoder|decoder|compress|decompress|entropy|huffman|reed[-_ ]solomon|ldpc"),
    ("networking", r"ethernet|\bmac\b|tcp|udp|ip[-_ ]core|network|packet|switch|arp|rmii|rgmii"),
    ("storage", r"nvme|sata|sdio|sd[-_ ]card|emmc|flash[-_ ]controller|nand[-_ ]controller"),
    ("signal_processing", r"\bfft\b|\bfir\b|\biir\b|\bdsp\b|filter|cordic|modulat|demodulat|spectrum|convolution"),
    ("protocol_bridge", r"bridge|crossbar|interconnect|converter|adapter|axi[-_ ]to|ahb[-_ ]to|apb[-_ ]to|wishbone[-_ ]to"),
    ("bus", r"\baxi[0-9]*\b|\bahb\b|\bapb\b|wishbone|avalon|tilelink|amba"),
    ("debug", r"\bjtag\b|debug|trace|logic[-_ ]analy[sz]er|ila"),
    ("clock_reset", r"\bpll\b|clock|reset|cdc|synchroni[sz]er|frequency[-_ ]divider"),
    ("interrupt", r"interrupt|\birq\b|plic|clint"),
    ("timer", r"timer|counter|stopwatch|chronometer|watchdog|rtc|digital[-_ ]clock"),
    ("sensor", r"sensor|temperature|imu|radar|lidar|adc|dac|measurement"),
    ("peripheral", r"\buart\b|\bspi\b|\bi2c\b|\bgpio\b|\bcan\b|\busb\b|pwm|keypad|seven[-_ ]segment"),
    ("memory", r"\bfifo\b|\bsram\b|\bram\b|\brom\b|memory|register[-_ ]file"),
    ("arithmetic", r"\balu\b|adder|subtract|multipl|divider|floating[-_ ]point|bfloat|fpu|arithmetic|sqrt|mac[-_ ]unit"),
    ("control", r"controller|control|\bfsm\b|state[-_ ]machine|arbiter|scheduler|elevator|traffic[-_ ]light"),
]


def evidence_text(record: dict[str, Any]) -> str:
    identity = record.get("identity", {})
    build = record.get("build", {})
    semantics = record.get("rtl_semantics", {})
    source = record.get("source", {})
    terms = [
        identity.get("repository_name", ""), identity.get("project_key", ""),
        build.get("top_module", ""), *build.get("dependency_modules", []),
        *semantics.get("child_modules", []), *semantics.get("interfaces", []),
        *(unit.get("path", "") for unit in source.get("source_units", [])),
    ]
    return " ".join(str(term).replace("/", " ").replace("_", " ") for term in terms).lower()


def classify(record: dict[str, Any]) -> dict[str, Any]:
    text = evidence_text(record)
    for label, pattern in RULES:
        matches = sorted(set(match.group(0).strip() for match in re.finditer(pattern, text, re.I)))
        if matches:
            confidence = "HIGH" if len(matches) > 1 else "MEDIUM"
            return {
                "schema": ONTOLOGY_SCHEMA, "label": label,
                "confidence": confidence, "diversity_weight": CONFIDENCE_WEIGHTS[confidence],
                "evidence": matches[:12],
            }
    arithmetic = record.get("rtl_semantics", {}).get("arithmetic_ops", {})
    if sum(int(value or 0) for value in arithmetic.values()) >= 8:
        return {"schema": ONTOLOGY_SCHEMA, "label": "arithmetic", "confidence": "LOW", "diversity_weight": CONFIDENCE_WEIGHTS["LOW"], "evidence": ["arithmetic_op_density"]}
    return {"schema": ONTOLOGY_SCHEMA, "label": "misc_ip", "confidence": "LOW", "diversity_weight": CONFIDENCE_WEIGHTS["LOW"], "evidence": []}
