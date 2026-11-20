from pathlib import Path
import importlib,sys,pickle,json,shutil,time
import torch
base=Path("/score")
source=base/"small/out/dataset"
meta=json.loads((base/"small/metadata.json").read_text())
truth=pickle.load(open("/reference/"+meta["design"]+"/truth.pkl","rb"))
truth["design"],truth["platform"]=meta["design"],"gf180"
root=base/"semantic_controls";root.mkdir(exist_ok=True)
cases=["valid","short_attr","short_y","none_attr","list_attr","infinity","nan_label","duplicate_edges","missing"]
for case in cases:
 out=root/case/"dataset"
 if out.exists():shutil.rmtree(out)
 shutil.copytree(source,out)
 if case in ("short_attr","short_y","none_attr","list_attr","infinity","duplicate_edges"):
  path=out/"c_graph.pt";g=torch.load(path,weights_only=False)
  rel=next(r for r in g.edge_types if not r[1].startswith("rc_") and g[r].edge_index.shape[1])
  st=g[rel]
  if case=="short_attr":st.edge_attr=st.edge_attr[:,:2]
  elif case=="short_y":st.edge_y=st.edge_y[:-1]
  elif case=="none_attr":st.edge_attr=None
  elif case=="list_attr":st.edge_attr=st.edge_attr.tolist()
  elif case=="infinity":st.edge_attr[0,0]=float("inf")
  elif case=="duplicate_edges":
   for r in g.edge_types:
    if r[1].startswith("rc_"):continue
    st=g[r];st.edge_index=torch.cat([st.edge_index,st.edge_index],dim=1)
    for k in ("edge_attr","edge_y","edge_y_raw"):st[k]=torch.cat([st[k],st[k]])
  torch.save(g,path)
 elif case=="nan_label":
  path=out/"b_graph.pt";g=torch.load(path,weights_only=False)
  g["gate"].y[0,0]=float("nan");g["gate"].y_raw[0,0]=float("nan");torch.save(g,path)
 elif case=="missing":(out/"f_graph_homo.pt").unlink()
for mode,path in (("baseline","/agent/exp4_harness"),("candidate","/private/exp4_harness")):
 for name in ("scoring","check_format","adapter_defgraph","v2_defgraph_check","match"):sys.modules.pop(name,None)
 sys.path=[path]+[p for p in sys.path if not p.startswith("/private/") and not p.startswith("/agent/")]
 scoring=importlib.import_module("scoring")
 for case in cases:
  result=scoring.score_design(truth,root/case/"dataset")
  (root/case/(mode+".json")).write_text(json.dumps(result,indent=2,allow_nan=False,default=str))
  print(json.dumps({"mode":mode,"case":case,"strict":result["strict_full_pass"]}),flush=True)
summary=[]
for case in cases:
 a=json.loads((root/case/"baseline.json").read_text());b=json.loads((root/case/"candidate.json").read_text())
 row={"case":case,"entire_json_equal":a==b,"baseline_pass":a["strict_full_pass"],"candidate_pass":b["strict_full_pass"]}
 if a!=b:
  row["different_top_keys"]=[k for k in a if a[k]!=b.get(k)]
 summary.append(row)
(base/"semantic_controls_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary),flush=True)
