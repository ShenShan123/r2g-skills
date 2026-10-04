#!/usr/bin/env python3
"""Draw a technical block diagram: processing, evidence gates, recipe reuse."""
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from plot_style import configure, save, BLUE, TEAL, CORAL, INK
ROOT=Path(__file__).resolve().parents[1]
configure()
fig,ax=plt.subplots(figsize=(5.5,2.65))
fig.subplots_adjust(left=0,right=1,bottom=0,top=1)
ax.set(xlim=(0,550),ylim=(0,265));ax.axis('off')
EDGE='#687583'
def text(x,y,s,size=8,color=INK,bold=False,ha='center'):
 ax.text(x,y,s,fontsize=size,color=color,fontweight='bold' if bold else 'normal',ha=ha,va='center',linespacing=1.5)
def arrow(start,end,col=EDGE):
 ax.annotate('',xy=end,xytext=start,arrowprops={'arrowstyle':'->','mutation_scale':8,'linewidth':.75,'color':col,'shrinkA':0,'shrinkB':0})
def block(x,y,w,h,color=None):
 ax.add_patch(Rectangle((x,y),w,h,facecolor='white',edgecolor=color or EDGE,linewidth=.75))
# Development produces a frozen executable policy, consumed by physical repair.
text(12,250,'Recipe development',8,TEAL,True,ha='left')
for x,w,label in [(12,132,'LLM proposals'),(203,144,'Validation'),(406,132,'Frozen recipes')]:
 block(x,207,w,26);text(x+w/2,220,label,8)
arrow((144,220),(203,220));arrow((347,220),(406,220))
ax.plot([472,472,275],[207,189,189],lw=.75,color=TEAL)
arrow((275,189),(275,169),TEAL)
text(372,181,'reuse',7.2,TEAL)
# The main path uses ordinary process blocks. Arrows name the handoff data.
stages=[(12,BLUE,'RTL acquisition','Discover and deduplicate\nResolve source closure','Synthesis qualification','Source record'),
        (203,TEAL,'Physical design','Run ORFS\nDiagnose and repair','Strict physical checks','Run record'),
        (406,CORAL,'Graph construction','Align identities\nExtract features & labels','Core semantic audit','Four-stage graphs')]
for x,col,title,body,gate,record in stages:
 width=144 if x==203 else 132
 center=x+width/2
 block(x,101,width,68)
 text(center,153,title,8.0,col,True)
 text(center,124,body,7.7)
 arrow((center,101),(center,84))
 text(center,74,gate,7.7,col)
 text(center,56,record,7.5)
arrow((144,135),(203,135));text(173.5,153,'Qualified\nRTL',6.7)
arrow((347,135),(406,135));text(376.5,153,'Physical\nartifacts',6.7)
# Environment and provenance apply to every processing stage.
ax.plot([12,538],[35,35],color='#C5CDD5',lw=.65)
text(275,18,'Shared environment: pinned tools, platform configuration and artifact provenance',7.6)
save(fig,ROOT,'workflow')
print('Rendered an icon-free technical workflow with data handoffs and acceptance checks.')
