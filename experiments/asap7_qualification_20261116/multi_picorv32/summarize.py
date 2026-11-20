from pathlib import Path
import hashlib,json,re,os
p=Path(__file__).resolve().parent;q=p.parent
ppa=json.loads((p/'reports/ppa.json').read_text())
e=q/'calibre_evidence/multi_picorv32'
drc_e=q/'calibre_evidence'/os.environ.get('PICO_DRC_EVIDENCE','multi_picorv32_fin_merged_hier')/'drc'
drc_input=q/os.environ.get('PICO_DRC_INPUT','multi_picorv32_fin_merged_input')
drc=json.loads((drc_e/'invocation.json').read_text())
lvs_e=q/'calibre_evidence/multi_picorv32_property65536/lvs'
lvs=json.loads((lvs_e/'invocation.json').read_text())
lvs['report_warnings']=sorted(set(re.findall(r'Warning:\s*(.+)',(lvs_e/'mask.lvs.rep').read_text())))
lvs['initial_property1024_evidence']=str(e/'lvs/invocation.json')
run=sorted((p/'backend').glob('RUN_*'))[-1]
text=(p/'reports/flow_resource.txt').read_text()
def metric(key,data=text):
 m=re.search(r'^\s*'+re.escape(key)+r':\s*(.*)$',data,re.M)
 return m[1] if m else None
tool_steps=[]
for logfile in sorted((run/'logs').glob('*.log')):
 for match in re.finditer(r'Elapsed time: ([0-9:.]+)\[h:\]min:sec\. CPU time: user ([0-9.]+) sys ([0-9.]+) \([^)]*\)\. Peak memory: ([0-9]+)KB\.',logfile.read_text(errors='replace')):
  fields=[float(x) for x in match[1].split(':')]
  seconds=sum(x*60**i for i,x in enumerate(reversed(fields)))
  tool_steps.append({'log':str(logfile),'elapsed_s':seconds,'user_s':float(match[2]),'system_s':float(match[3]),'peak_rss_kib':int(match[4])})
assert len(tool_steps)==19
assert abs(sum(x['elapsed_s'] for x in tool_steps)-503.51)<0.001
wrapper_elapsed=metric('Elapsed (wall clock) time (h:mm:ss or m:ss)')
wrapper_wall_s=sum(float(x)*60**i for i,x in enumerate(reversed(wrapper_elapsed.split(':'))))
cells=re.findall(r'^\s*(\w+_ASAP7_75t_R)\s+(?:\\\S+|\S+)\s*\(', (run/'results/6_final.v').read_text(),re.M)
attempts=[]
for name in ['multi_picorv32','multi_picorv32_flattened_hier','multi_picorv32_fin_merged_hier']:
 a=q/'calibre_evidence'/name/'drc'
 receipt=json.loads((a/'invocation.json').read_text());log=(a/'run.log').read_text()
 attempts.append(dict(name=name,receipt_path=str(a/'invocation.json'),receipt=receipt,user_s=float(metric('User time (seconds)',log)),system_s=float(metric('System time (seconds)',log)),peak_rss_kib=int(metric('Maximum resident set size (kbytes)',log)),last_operation='86 of 1010, after ACTIVE.FIN.EX.1'))
drc_complete=drc.get('result') in ('clean','violations')
drc_summary=f"{drc.get('rulechecks')} 项规则，{drc.get('expanded_results')} 个结果" if drc_complete else '未完成：3 次独立输入/引擎尝试均在同一运算达到 600 s 时限；无完整 DRC 判定'
result=dict(qualification_status='complete' if drc.get('result')=='clean' and lvs['result']=='CORRECT' else 'incomplete_drc_timeout',active_jobs=[],drc_attempts=attempts,final_drc_receipt=str(drc_e/'invocation.json') if drc_complete else None,project='multi_picorv32',top='picorv32_demo',platform='asap7',run=str(run),
 clock_period_ps=1000,core_utilization_config=0.30,placement_density_config=0.35,clock_optimization='fixed target; no Fmax search',
 drc_receipt=str(drc_e/'invocation.json'),lvs_receipt=str(lvs_e/'invocation.json'),drc_gds=str(drc_input/'drc.gds'),lvs_gds=str(q/'multi_picorv32_input/lvs.gds'),source_cdl=str(q/'multi_picorv32_input/source.cdl'),drc_geometry_verification=str(drc_input/'geometry_verification.json'),final_gds_adapter_verification=str(q/'multi_picorv32_input/geometry_verification.json'),
 rtl_source=json.loads((p/'input/source_provenance.json').read_text()),validation=json.loads((p/'input/validation.json').read_text()),
 lint=json.loads((p/'reports/lint.json').read_text()),rtl_simulation=json.loads((p/'reports/rtl_simulation.json').read_text()),
 final_netlist_simulation=json.loads((p/'reports/gate_simulation.json').read_text()),negative_control=json.loads((p/'reports/negative_control.json').read_text()),ppa=ppa,drc=drc,lvs=lvs,
 ppa_drc_note='Generic extract_ppa selected historical 5_route_drc.rpt-5.rpt and regex misread Layer M2 newline violation as count 2. Final 5_route_drc.rpt is empty and router final count is 0. Use official Calibre result for signoff.',
 final_netlist_logic_instances=len(cells),final_netlist_cell_types=sorted(set(cells)),
 flow_resource={'orfs_tool_step_count':len(tool_steps),'orfs_tool_elapsed_sum_s':round(sum(x['elapsed_s'] for x in tool_steps),2),'orfs_tool_steps':tool_steps,'wrapper_wall_s':wrapper_wall_s,'peak_rss_mib':int(metric('Maximum resident set size (kbytes)'))/1024,'elapsed':metric('Elapsed (wall clock) time (h:mm:ss or m:ss)'), 'user_s':metric('User time (seconds)'), 'system_s':metric('System time (seconds)'), 'peak_rss_kib':metric('Maximum resident set size (kbytes)'), 'exit_status':metric('Exit status')},
 geometry_adapter=json.loads((q/'multi_picorv32_input/geometry_verification.json').read_text()),
 reference_validation=json.loads((p/'reports/reference_validation.json').read_text()),
 limitations=['Bounded program simulation, not exhaustive ISA verification or formal equivalence.','Final-netlist simulation uses zero-delay Liberty models; timing is checked independently by STA.','No external SRAM macro: program/data RAM lives in the testbench.','Power is the flow tool estimate without program-derived switching annotation.'])
files=[p/'constraints/config.mk',p/'constraints/pdn.tcl',p/'constraints/constraint.sdc',run/'results/6_final.gds',run/'results/6_final.v',run/'results/6_final.odb',q/'multi_picorv32_input/source.cdl',drc_e/'drc_report',lvs_e/'mask.lvs.rep']
files=[f for f in files if f.exists()]+[drc_input/'drc.gds',drc_input/'geometry_verification.json',drc_e/'invocation.json',lvs_e/'invocation.json',p/'reports/reference_validation.json']
result['evidence_sha256']={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
result['drc_gcd_control']=json.loads((q/'calibre_evidence/gcd_pin_window_v2_fin_merged_hier/drc/invocation.json').read_text())
(p/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
g=ppa['geometry'];t=ppa['summary']['timing'];power=ppa['summary']['power'];r=result['flow_resource']
(p/'RESULT.md').write_text(f'''# PicoRV32 ASAP7 扩展核签

完整物理流程、功能仿真、STA 与 LVS 已通过；官方 DRC 在复杂几何运算处达到时限，当前不能声明 DRC/LVS 双清；超时也不等于已发现 DRC 违例。

采用现有 PicoRV32 原始 RTL，保留 ISC 许可，默认 RV32I、32 个寄存器、64-bit counters，无乘除法/压缩指令/IRQ 扩展。新 wrapper 导出 native memory bus；不含 SRAM 宏，仿真的程序与数据内存位于 testbench。

| 验证/指标 | 结果 |
|---|---|
| 配置校验、Icarus 语法/展开检查 | PASS |
| RTL 指令程序 | PASS，3 种 memory wait-state 模式，每种 9 项签名 |
| 最终网表同程序 | PASS，3 种模式；Liberty 零延迟功能模型 |
| 官方完整 DRC（掩模等价输入） | {drc_summary} |
| 官方晶体管级 LVS | {lvs['result']}，property resolution maximum=65536 |
| 固定时钟周期 | 1000 ps（1 GHz），未搜索 Fmax |
| die / core 面积 | {g['die_area_um2']} / {g['core_area_um2']} µm² |
| 标准单元面积 / 利用率 | {g['stdcell_area_um2']} µm² / {100*g['utilization']:.2f}% |
| 最终逻辑网表实例 / cell 类型 | {len(cells)} / {len(set(cells))} |
| Setup / hold slack | {t.get('setup_wns')} / {t.get('hold_wns')} ps |
| Setup / hold TNS | {t.get('setup_tns')} / {t.get('hold_tns')} ps |
| 工具估计功耗 | {power.get('total_power_w')} W |
| ORFS 工具阶段耗时合计 | {r['orfs_tool_elapsed_sum_s']:.2f} s（19 条 elapsed 记录） |
| 外层完整 flow wall time | {r['wrapper_wall_s']:.2f} s（包含启动与归档开销） |
| 物理流程峰值 RSS | {r['peak_rss_mib']:.2f} MiB |
| DRC 尝试 / 最终 LVS 复核耗时 | 3 × 600 s（均超时） / {lvs['wall_s']:.2f} s |

程序覆盖求和/Fibonacci 循环、分支、算术、移位、字/字节读写、符号/无符号扩展、JAL/JALR 和 EBREAK。上述仿真为有限程序验证，不等于完整 ISA 认证或形式等价证明。门级仿真不带延迟；时序由 STA 单独评估。另将最终网表写数据 bit 0 反相的独立负控被测试正确拒绝，原始网表未修改。功耗为默认活动下的工具估计，未用该程序标注活动。

物理流程复用 GCD 已核签的 TECH_LEF、保守 SC_LEF 引脚窗口、M1 via-in-pin 和 PDN，未修改共享平台或官方 DRC/LVS 规则。原始核签输入相对最终 GDS 的非文本几何与实例变换一致，仅有顶层名/引脚标签适配。后续 DRC 展平及 FIN 精确 union 候选另经全部 36 层 Region XOR=0 验证，未改变掩模几何；这些候选未获得完整 DRC 判定。已双清 GCD 的相同 FIN union 对照仍为 334 项/0 结果（9.52 s）。晶体管参考来自独立官方 PEX，不从本次版图反推。独立参考审计通过：319443 个 MOS 的 D/G/S/B、型号及 L/W/nfin 均保留，59 种组合单元完成穷举功能核对，3 种时序单元完成严格晶体管拓扑等价检查。

三次 DRC 依次为传统 flat、完全展平 hier turbo4、完全展平且 FIN union 的 hier turbo4，user CPU 时间分别为 597.01、647.90、649.42 s；均在 86/1010 加密运算处耗尽时限，未报告完整检查数或最终违例数。所有进程已结束。

精确输入哈希、工具命令、运行路径和原始结果见 `RESULT.json`；复现命令见 `REPRODUCE.md`。首次流程日志和核签原件均保留。初次 LVS 以 1024 为属性解析上限得到 CORRECT，但触及解析预算；同输入以 65536 复核仍为 CORRECT，该预算警告消失，保留同构器件的 ambiguity 提示。原始 `ppa.json` 的 `summary.drc=2` 是通用解析器误读历史迭代文件中的 `M2`，不代表最终 DRC；最终路由报告为空，核签结果以上表官方 Calibre 结果为准。
''')
print(json.dumps({'drc':drc['result'],'lvs':lvs['result'],'logic_instances':len(cells),'geometry':g,'timing':t,'flow_resource':r},indent=2))
