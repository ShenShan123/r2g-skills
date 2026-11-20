* SPICE NETLIST
***************************************

.SUBCKT VIA_VIA23
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT VIA_VIA34
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT TAPCELL_ASAP7_75t_R
** N=6 EP=0 IP=0 FDC=0
*.CALIBRE ISOLATED NETS: VSS VDD
.ENDS
***************************************
.SUBCKT ICV_1
** N=2 EP=0 IP=4 FDC=0
.ENDS
***************************************
.SUBCKT ICV_2
** N=2 EP=0 IP=4 FDC=0
.ENDS
***************************************
.SUBCKT VIA_VIA12
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_3
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_4
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT FILLERxp5_ASAP7_75t_R
** N=4 EP=0 IP=0 FDC=0
*.CALIBRE ISOLATED NETS: VSS VDD
.ENDS
***************************************
.SUBCKT ICV_5
** N=2 EP=0 IP=4 FDC=0
.ENDS
***************************************
.SUBCKT FILLER_ASAP7_75t_R
** N=6 EP=0 IP=0 FDC=0
*.CALIBRE ISOLATED NETS: VSS VDD
.ENDS
***************************************
.SUBCKT ICV_6
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_7
** N=2 EP=0 IP=4 FDC=0
.ENDS
***************************************
.SUBCKT DECAPx1_ASAP7_75t_R VSS VDD
** N=8 EP=2 IP=0 FDC=2
M0 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M1 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT INVx1_ASAP7_75t_R VSS VDD A Y
** N=8 EP=4 IP=0 FDC=2
M0 Y A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 Y A VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_8
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_9 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=2
X1 1 2 DECAPx1_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT BUFx2_ASAP7_75t_R VSS VDD A Y
** N=9 EP=4 IP=0 FDC=6
M0 VSS A 5 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 Y 5 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS 5 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 VDD A 5 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M4 Y 5 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M5 VDD 5 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT DECAPx2_ASAP7_75t_R VSS VDD
** N=8 EP=2 IP=0 FDC=4
M0 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M1 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M2 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M3 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_10 1 2 3 4
*.CALIBRE ICV_CELL 9
** N=4 EP=4 IP=6 FDC=6
X1 1 2 3 4 BUFx2_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_11
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_12
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_13 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=4
X1 1 2 DECAPx2_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_14 1 2 3 4
*.CALIBRE ICV_CELL 9
** N=4 EP=4 IP=6 FDC=2
X1 1 2 3 4 INVx1_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-88
.ENDS
***************************************
.SUBCKT DECAPx4_ASAP7_75t_R VSS VDD
** N=8 EP=2 IP=0 FDC=8
M0 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M1 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M2 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M3 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M4 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M5 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M6 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M7 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT VIA_VIA12_DOWN
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_15
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_16
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_17
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_18 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_19
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_20
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT DECAPx6_ASAP7_75t_R VSS VDD
** N=8 EP=2 IP=0 FDC=12
M0 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M1 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M2 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=108 $D=1
M3 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2228 $Y=108 $D=1
M4 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2444 $Y=108 $D=1
M5 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2660 $Y=108 $D=1
M6 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M7 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M8 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M9 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M10 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M11 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_21
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT AND4x1_ASAP7_75t_R VSS VDD A B C D Y
** N=15 EP=7 IP=0 FDC=10
M0 9 A 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 10 B 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 11 C 10 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 VSS D 11 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 Y 8 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 8 A VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M6 VDD B 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M7 8 C VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M8 VDD D 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M9 Y 8 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_22
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_23
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_24
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_25 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=12
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_26 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=14
X0 1 2 DECAPx1_ASAP7_75t_R $T=3024 0 0 0 $X=3024 $Y=-88
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT XNOR2x2_ASAP7_75t_R VSS VDD A B Y
** N=18 EP=5 IP=0 FDC=14
M0 VSS 6 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 9 A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 7 B 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 6 B VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1364 $Y=108 $D=1
M4 VSS A 6 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1580 $Y=108 $D=1
M5 Y 7 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M6 VSS 7 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=108 $D=1
M7 8 6 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M8 VDD A 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M9 8 B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M10 10 B 6 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
M11 VDD A 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
M12 Y 7 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=648 $D=0
M13 VDD 7 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_27
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_28
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_29 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=14
X0 1 2 ICV_9 $T=3024 0 0 0 $X=3024 $Y=-88
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_30 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_31
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_32 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=16
X0 1 2 DECAPx2_ASAP7_75t_R $T=3024 0 0 0 $X=3024 $Y=-88
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_33 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=4
X1 1 2 DECAPx2_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_34
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_35
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT DFFHQNx1_ASAP7_75t_R VSS VDD CLK D QN
** N=29 EP=5 IP=0 FDC=24
M0 VSS CLK 6 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 7 6 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=108 $D=1
M2 12 D VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M3 9 6 12 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M4 15 7 9 VSS nmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=1796 $Y=108 $D=1
M5 VSS 8 15 VSS nmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2012 $Y=108 $D=1
M6 8 9 VSS VSS nmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2228 $Y=108 $D=1
M7 11 7 8 VSS nmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2444 $Y=108 $D=1
M8 14 6 11 VSS nmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2660 $Y=108 $D=1
M9 VSS 10 14 VSS nmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=3092 $Y=108 $D=1
M10 10 11 VSS VSS nmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=3308 $Y=108 $D=1
M11 QN 11 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3956 $Y=108 $D=1
M12 VDD CLK 6 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M13 7 6 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M14 16 D VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M15 9 7 16 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
M16 13 6 9 VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=1580 $Y=864 $D=0
M17 VDD 8 13 VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2012 $Y=864 $D=0
M18 8 9 VDD VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2228 $Y=864 $D=0
M19 11 6 8 VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2660 $Y=864 $D=0
M20 17 7 11 VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=2876 $Y=864 $D=0
M21 VDD 10 17 VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=3092 $Y=864 $D=0
M22 10 11 VDD VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=3308 $Y=864 $D=0
M23 QN 11 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3956 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_36
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_37
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_38
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_39
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT DECAPx10_ASAP7_75t_R VSS VDD
** N=8 EP=2 IP=0 FDC=20
M0 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2444 $Y=108 $D=1
M1 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2660 $Y=108 $D=1
M2 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2876 $Y=108 $D=1
M3 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3092 $Y=108 $D=1
M4 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3308 $Y=108 $D=1
M5 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3524 $Y=108 $D=1
M6 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3740 $Y=108 $D=1
M7 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3956 $Y=108 $D=1
M8 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=4172 $Y=108 $D=1
M9 3 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=4388 $Y=108 $D=1
M10 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M11 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M12 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M13 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M14 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M15 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
M16 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
M17 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=648 $D=0
M18 VDD 3 4 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=648 $D=0
M19 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2228 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT OR4x1_ASAP7_75t_R VSS VDD D C B A Y
** N=15 EP=7 IP=0 FDC=10
M0 VSS 8 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 8 D VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS C 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 8 B VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 VSS A 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 VDD 8 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M6 9 D VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M7 10 C 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M8 11 B 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M9 8 A 11 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_40
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT VIA_VIA56
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT VIA_VIA45
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_41
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT AND3x1_ASAP7_75t_R VSS VDD A B C Y
** N=13 EP=6 IP=0 FDC=8
M0 8 A 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 9 B 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS C 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 Y 7 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 VDD A 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M5 7 B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M6 VDD C 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M7 Y 7 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT NAND2x1_ASAP7_75t_R VSS VDD A B Y
** N=10 EP=5 IP=0 FDC=6
M0 VSS A 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 6 A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 Y B 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 6 B Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 VDD A Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M5 Y B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_42
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT AND2x2_ASAP7_75t_R VSS VDD B A Y
** N=11 EP=5 IP=0 FDC=8
M0 7 B 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 VSS A 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 Y 6 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 VSS 6 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 6 B VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M5 VDD A 6 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M6 Y 6 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M7 VDD 6 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT NOR2x1_ASAP7_75t_R VSS VDD A B Y
** N=10 EP=5 IP=0 FDC=6
M0 VSS A Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M1 Y B VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M2 VDD A 6 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M3 6 A VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M4 Y B 6 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M5 6 B Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_43 1 2 3 4 5
*.CALIBRE ICV_CELL 9
** N=5 EP=5 IP=7 FDC=8
X1 1 2 3 4 5 AND2x2_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_44
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_45 1 2 3 4 5
*.CALIBRE ICV_CELL 9
** N=5 EP=5 IP=7 FDC=26
X0 1 2 ICV_9 $T=0 0 0 0 $X=0 $Y=-88
X1 1 2 3 4 5 DFFHQNx1_ASAP7_75t_R $T=1080 0 0 0 $X=1080 $Y=-88
.ENDS
***************************************
.SUBCKT OAI22x1_ASAP7_75t_R VSS VDD B2 B1 A2 A1 Y
** N=15 EP=7 IP=0 FDC=16
M0 VSS B2 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 8 B1 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS B1 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 8 B2 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 Y A2 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 8 A1 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M6 Y A1 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M7 8 A2 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M8 9 B2 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M9 VDD B1 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M10 10 B1 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M11 Y B2 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M12 11 A2 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M13 VDD A1 11 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
M14 12 A1 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
M15 Y A2 12 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT VIA_VIA67
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_46
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_47
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_48
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_49
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_50
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_51 1 2 3 4 5
*.CALIBRE ICV_CELL 9
** N=5 EP=5 IP=7 FDC=24
X1 1 2 3 4 5 DFFHQNx1_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-88
.ENDS
***************************************
.SUBCKT HAxp5_ASAP7_75t_R VSS VDD A B CON SN
** N=17 EP=6 IP=0 FDC=10
M0 8 A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 CON B 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS B 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M3 7 A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M4 SN CON 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M5 CON A VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M6 VDD B CON VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M7 9 B SN VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M8 VDD A 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
M9 SN CON VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT OA211x2_ASAP7_75t_R VSS VDD A2 A1 B C Y
** N=15 EP=7 IP=0 FDC=12
M0 8 A2 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 9 A1 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 10 B 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 VSS C 10 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 Y 8 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 VSS 8 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M6 11 A2 8 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M7 VDD A1 11 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M8 8 B VDD VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=716 $Y=864 $D=0
M9 VDD C 8 VDD pmos_rvt L=2e-08 W=2.7e-08 nfin=1 $X=932 $Y=864 $D=0
M10 Y 8 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M11 VDD 8 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_52
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_53
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_54 1 2 3 4 5
*.CALIBRE ICV_CELL 9
** N=5 EP=5 IP=7 FDC=24
X1 1 2 3 4 5 DFFHQNx1_ASAP7_75t_R $T=432 0 0 0 $X=432 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_55 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=24
X0 1 2 DECAPx2_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT NAND3x1_ASAP7_75t_R VSS VDD C B A Y
** N=12 EP=6 IP=0 FDC=12
M0 7 C VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 VSS C 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 7 C VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 8 B 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 7 B 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 8 B 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M6 Y A 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M7 8 A Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M8 Y A 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=108 $D=1
M9 Y C VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M10 VDD B Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M11 Y A VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_56 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=4
X1 1 2 DECAPx2_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_57 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=28
X0 1 2 DECAPx4_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_58
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_59
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT AO221x1_ASAP7_75t_R VSS VDD B1 B2 C A1 A2 Y
** N=21 EP=8 IP=0 FDC=12
M0 12 B1 9 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 VSS B2 12 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=108 $D=1
M2 9 C VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=716 $Y=108 $D=1
M3 13 A1 9 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=932 $Y=108 $D=1
M4 VSS A2 13 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1148 $Y=108 $D=1
M5 Y 9 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M6 9 B1 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M7 10 B2 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M8 11 C 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M9 VDD A1 11 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M10 11 A2 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M11 Y 9 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_60
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT AO22x1_ASAP7_75t_R VSS VDD A1 A2 B2 B1 Y
** N=19 EP=7 IP=0 FDC=10
M0 10 A1 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 8 A2 10 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=108 $D=1
M2 11 B2 8 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=716 $Y=108 $D=1
M3 VSS B1 11 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=932 $Y=108 $D=1
M4 Y 8 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M5 VDD A1 9 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M6 9 A2 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M7 8 B2 9 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=716 $Y=756 $D=0
M8 9 B1 8 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=932 $Y=756 $D=0
M9 Y 8 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_61 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=32
X0 1 2 DECAPx6_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_62 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT AO21x1_ASAP7_75t_R VSS VDD A1 A2 B Y
** N=13 EP=6 IP=0 FDC=8
M0 9 A1 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 7 A2 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS B 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 Y 7 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 7 A1 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M5 8 A2 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M6 VDD B 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M7 Y 7 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_63
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_64
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT OA21x2_ASAP7_75t_R A1 VSS VDD A2 B Y
** N=13 EP=6 IP=0 FDC=10
M0 7 A1 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 8 A2 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS B 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 Y 7 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 VSS 7 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 9 A1 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M6 7 A2 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M7 VDD B 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M8 Y 7 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M9 VDD 7 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_65
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT OAI21x1_ASAP7_75t_R VSS VDD B A1 A2 Y
** N=13 EP=6 IP=0 FDC=12
M0 7 B Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 VSS A1 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 7 A2 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 VSS A2 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 7 A1 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 Y B 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M6 Y B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M7 8 A1 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M8 VDD A2 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M9 9 A2 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M10 Y A1 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M11 VDD B Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_66
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_67 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=40
X0 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_68
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT XOR2x2_ASAP7_75t_R VSS VDD B A Y
** N=18 EP=5 IP=0 FDC=14
M0 8 6 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 VSS B 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 8 A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 9 A 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M4 VSS B 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M5 Y 7 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M6 VSS 7 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=108 $D=1
M7 VDD 6 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M8 10 B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M9 7 A 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M10 6 A VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1364 $Y=756 $D=0
M11 VDD B 6 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1580 $Y=756 $D=0
M12 Y 7 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=648 $D=0
M13 VDD 7 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT AND5x1_ASAP7_75t_R VSS VDD A B C D E Y
** N=17 EP=8 IP=0 FDC=12
M0 10 A 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 11 B 10 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 12 C 11 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 13 D 12 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 VSS E 13 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 Y 9 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M6 VDD A 9 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M7 9 B VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M8 VDD C 9 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=716 $Y=756 $D=0
M9 9 D VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=932 $Y=756 $D=0
M10 VDD E 9 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1148 $Y=756 $D=0
M11 Y 9 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT AOI211x1_ASAP7_75t_R VSS VDD A2 A1 B C Y
** N=18 EP=7 IP=0 FDC=12
M0 10 A2 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M1 VSS A1 10 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M2 Y B VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M3 VSS C Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=108 $D=1
M4 8 A2 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M5 VDD A2 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M6 8 A1 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M7 VDD A1 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M8 8 B 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
M9 9 B 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=648 $D=0
M10 Y C 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=648 $D=0
M11 9 C Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2228 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_69 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=20
X1 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_70
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT BUFx4_ASAP7_75t_R VSS VDD A Y
** N=9 EP=4 IP=0 FDC=10
M0 VSS A 5 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 Y 5 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS 5 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 Y 5 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 VSS 5 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 VDD A 5 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M6 Y 5 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M7 VDD 5 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M8 Y 5 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M9 VDD 5 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT OR3x1_ASAP7_75t_R VSS VDD A B C Y
** N=13 EP=6 IP=0 FDC=8
M0 VSS A 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 7 B VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS C 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 Y 7 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 8 A 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M5 9 B 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M6 VDD C 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M7 Y 7 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT AO32x1_ASAP7_75t_R VSS VDD A3 A2 A1 B1 B2 Y
** N=17 EP=8 IP=0 FDC=12
M0 VSS 9 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 11 A3 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 12 A2 11 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 9 A1 12 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 13 B1 9 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1148 $Y=108 $D=1
M5 VSS B2 13 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1364 $Y=108 $D=1
M6 VDD 9 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M7 10 A3 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M8 VDD A2 10 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=716 $Y=756 $D=0
M9 10 A1 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=932 $Y=756 $D=0
M10 9 B1 10 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1148 $Y=756 $D=0
M11 10 B2 9 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1364 $Y=756 $D=0
.ENDS
***************************************
.SUBCKT INVx3_ASAP7_75t_R VSS VDD A
** N=8 EP=3 IP=0 FDC=6
M0 Y A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 VSS A Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 Y A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 Y A VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M4 VDD A Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M5 Y A VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT OR2x2_ASAP7_75t_R VSS VDD B A Y
** N=11 EP=5 IP=0 FDC=8
M0 6 B VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 VSS A 6 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=108 $D=1
M2 Y 6 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 VSS 6 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 7 B 6 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M5 VDD A 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M6 Y 6 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M7 VDD 6 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_71
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_72 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=80
X0 1 2 ICV_67 $T=0 0 0 0 $X=0 $Y=-88
X1 1 2 ICV_67 $T=9504 0 0 0 $X=9504 $Y=-88
.ENDS
***************************************
.SUBCKT AOI21x1_ASAP7_75t_R VSS VDD B A1 A2 Y
** N=13 EP=6 IP=0 FDC=12
M0 Y B VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 8 A1 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS A2 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 9 A2 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 Y A1 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 VSS B Y VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1364 $Y=108 $D=1
M6 7 B Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M7 VDD A1 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M8 7 A2 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M9 VDD A2 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M10 7 A1 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M11 Y B 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT XNOR2x1_ASAP7_75t_R VSS VDD B A Y
** N=18 EP=5 IP=0 FDC=16
M0 8 B VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 6 A 8 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M2 VSS A 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M3 7 B VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M4 Y 6 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=108 $D=1
M5 7 6 Y VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=108 $D=1
M6 VSS B 7 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=108 $D=1
M7 7 A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2228 $Y=108 $D=1
M8 6 B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M9 VDD A 6 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M10 9 A Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M11 VDD B 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
M12 Y 6 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1580 $Y=648 $D=0
M13 VDD 6 Y VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1796 $Y=648 $D=0
M14 10 B VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=648 $D=0
M15 Y A 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2228 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT OR5x1_ASAP7_75t_R VSS VDD A B C D E Y
** N=17 EP=8 IP=0 FDC=12
M0 VSS A 9 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 9 B VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=108 $D=1
M2 VSS C 9 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=716 $Y=108 $D=1
M3 9 D VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=932 $Y=108 $D=1
M4 VSS E 9 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1148 $Y=108 $D=1
M5 Y 9 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M6 10 A 9 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
M7 11 B 10 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=648 $D=0
M8 12 C 11 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M9 13 D 12 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M10 VDD E 13 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M11 Y 9 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_73 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=80
X0 1 2 ICV_67 $T=0 0 0 0 $X=0 $Y=-88
X1 1 2 ICV_67 $T=0 2160 1 0 $X=0 $Y=992
.ENDS
***************************************
.SUBCKT ICV_74 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=160
X0 1 2 ICV_73 $T=0 0 0 0 $X=0 $Y=-88
X1 1 2 ICV_73 $T=9504 0 0 0 $X=9504 $Y=-88
.ENDS
***************************************
.SUBCKT CKINVDCx6p67_ASAP7_75t_R VSS VDD A
** N=14 EP=3 IP=0 FDC=44
M0 Y A VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=108 $D=1
M1 VSS A Y VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=108 $D=1
M2 4 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=108 $D=1
M3 VSS 4 4 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=108 $D=1
M4 4 5 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=108 $D=1
M5 VSS 5 4 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=108 $D=1
M6 Y A VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1580 $Y=108 $D=1
M7 VSS A Y VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1796 $Y=108 $D=1
M8 6 6 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=108 $D=1
M9 VSS 6 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2228 $Y=108 $D=1
M10 6 7 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2444 $Y=108 $D=1
M11 VSS 7 6 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2660 $Y=108 $D=1
M12 Y A VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=2876 $Y=108 $D=1
M13 VSS A Y VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=3092 $Y=108 $D=1
M14 9 8 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3308 $Y=108 $D=1
M15 VSS 8 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3524 $Y=108 $D=1
M16 9 9 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3740 $Y=108 $D=1
M17 VSS 9 9 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3956 $Y=108 $D=1
M18 Y A VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4172 $Y=108 $D=1
M19 VSS A Y VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4388 $Y=108 $D=1
M20 Y A VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4604 $Y=108 $D=1
M21 VSS A Y VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4820 $Y=108 $D=1
M22 Y A VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=284 $Y=756 $D=0
M23 VDD A Y VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=500 $Y=756 $D=0
M24 5 4 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=716 $Y=648 $D=0
M25 VDD 4 5 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=932 $Y=648 $D=0
M26 5 5 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1148 $Y=648 $D=0
M27 VDD 5 5 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=1364 $Y=648 $D=0
M28 Y A VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1580 $Y=756 $D=0
M29 VDD A Y VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=1796 $Y=756 $D=0
M30 7 6 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2012 $Y=648 $D=0
M31 VDD 6 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2228 $Y=648 $D=0
M32 7 7 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2444 $Y=648 $D=0
M33 VDD 7 7 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=2660 $Y=648 $D=0
M34 Y A VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=2876 $Y=756 $D=0
M35 VDD A Y VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=3092 $Y=756 $D=0
M36 8 8 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3308 $Y=648 $D=0
M37 VDD 8 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3524 $Y=648 $D=0
M38 8 9 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3740 $Y=648 $D=0
M39 VDD 9 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=3956 $Y=648 $D=0
M40 Y A VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4172 $Y=756 $D=0
M41 VDD A Y VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4388 $Y=756 $D=0
M42 Y A VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4604 $Y=756 $D=0
M43 VDD A Y VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=4820 $Y=756 $D=0
.ENDS
***************************************
.SUBCKT VIA_via1_2_16200_18_1_450_36_36
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_75
** N=2 EP=0 IP=4 FDC=0
.ENDS
***************************************
.SUBCKT uart VDD VSS s_axis_tdata[5] s_axis_tready s_axis_tdata[2] txd s_axis_tvalid s_axis_tdata[3] rst s_axis_tdata[0] tx_busy s_axis_tdata[1] prescale[1] prescale[12] rx_busy rx_frame_error prescale[15] prescale[14] s_axis_tdata[6] s_axis_tdata[7]
+ prescale[7] prescale[5] prescale[0] prescale[3] prescale[4] prescale[2] rxd prescale[9] prescale[8] prescale[6] prescale[10] m_axis_tdata[6] m_axis_tdata[4] prescale[13] m_axis_tready m_axis_tdata[7] m_axis_tdata[2] m_axis_tdata[1] m_axis_tdata[0] m_axis_tvalid
+ prescale[11] m_axis_tdata[3] rx_overrun_error s_axis_tdata[4] m_axis_tdata[5] clk
** N=753 EP=46 IP=4603 FDC=17037
M0 686 3 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=14756 $Y=42768 $D=1
M1 47 4 686 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=15404 $Y=42768 $D=1
M2 687 5 47 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=15620 $Y=42768 $D=1
M3 VSS 6 687 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=16268 $Y=42768 $D=1
M4 VSS 7 47 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=16916 $Y=42876 $D=1
M5 21 8 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=24692 $Y=56268 $D=1
M6 52 9 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=24908 $Y=40608 $D=1
M7 VSS 8 21 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=24908 $Y=56268 $D=1
M8 VSS 9 52 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25124 $Y=40608 $D=1
M9 688 10 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25124 $Y=56268 $D=1
M10 689 11 688 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25340 $Y=56268 $D=1
M11 690 12 689 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25556 $Y=56268 $D=1
M12 52 13 53 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25772 $Y=40608 $D=1
M13 8 14 690 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25772 $Y=56268 $D=1
M14 53 13 52 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25988 $Y=40608 $D=1
M15 54 15 53 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=26204 $Y=40608 $D=1
M16 53 15 54 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=26420 $Y=40608 $D=1
M17 55 16 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=26636 $Y=47628 $D=1
M18 VSS 16 55 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=26852 $Y=47628 $D=1
M19 54 17 56 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27068 $Y=40608 $D=1
M20 55 16 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27068 $Y=47628 $D=1
M21 56 17 54 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27284 $Y=40608 $D=1
M22 VSS 16 55 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27284 $Y=47628 $D=1
M23 19 18 56 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27500 $Y=40608 $D=1
M24 55 16 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27500 $Y=47628 $D=1
M25 56 18 19 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27716 $Y=40608 $D=1
M26 VSS 16 55 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27716 $Y=47628 $D=1
M27 55 16 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27932 $Y=47628 $D=1
M28 VSS 16 55 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=28148 $Y=47628 $D=1
M29 57 19 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=28364 $Y=40608 $D=1
M30 VSS 19 57 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=28580 $Y=40608 $D=1
M31 18 20 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30956 $Y=47088 $D=1
M32 VSS 20 18 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31172 $Y=47088 $D=1
M33 18 20 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31388 $Y=47088 $D=1
M34 VSS 20 18 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31604 $Y=47088 $D=1
M35 58 21 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31820 $Y=47088 $D=1
M36 VSS 21 58 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32036 $Y=47088 $D=1
M37 58 22 59 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32684 $Y=47088 $D=1
M38 59 22 58 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32900 $Y=47088 $D=1
M39 20 23 59 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33116 $Y=47088 $D=1
M40 59 23 20 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33332 $Y=47088 $D=1
M41 64 24 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41972 $Y=38448 $D=1
M42 VSS 25 64 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42188 $Y=38448 $D=1
M43 64 26 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42836 $Y=38448 $D=1
M44 30 27 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47588 $Y=23328 $D=1
M45 71 28 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=47588 $Y=45036 $D=1
M46 VSS 27 30 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47804 $Y=23328 $D=1
M47 VSS 28 71 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=47804 $Y=45036 $D=1
M48 30 27 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48020 $Y=23328 $D=1
M49 29 29 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48020 $Y=44928 $D=1
M50 VSS 27 30 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48236 $Y=23328 $D=1
M51 VSS 29 29 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48236 $Y=44928 $D=1
M52 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48452 $Y=23328 $D=1
M53 29 31 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48452 $Y=44928 $D=1
M54 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48668 $Y=23328 $D=1
M55 VSS 31 29 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48668 $Y=44928 $D=1
M56 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48884 $Y=23328 $D=1
M57 71 28 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=48884 $Y=45036 $D=1
M58 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49100 $Y=23328 $D=1
M59 VSS 28 71 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=49100 $Y=45036 $D=1
M60 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49316 $Y=23328 $D=1
M61 32 32 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49316 $Y=44928 $D=1
M62 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49532 $Y=23328 $D=1
M63 VSS 32 32 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49532 $Y=44928 $D=1
M64 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49748 $Y=23328 $D=1
M65 32 33 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49748 $Y=44928 $D=1
M66 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49964 $Y=23328 $D=1
M67 VSS 33 32 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49964 $Y=44928 $D=1
M68 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50180 $Y=23328 $D=1
M69 71 28 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=50180 $Y=45036 $D=1
M70 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50396 $Y=23328 $D=1
M71 VSS 28 71 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=50396 $Y=45036 $D=1
M72 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50612 $Y=23328 $D=1
M73 35 34 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50612 $Y=44928 $D=1
M74 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50828 $Y=23328 $D=1
M75 VSS 34 35 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50828 $Y=44928 $D=1
M76 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51044 $Y=23328 $D=1
M77 35 35 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51044 $Y=44928 $D=1
M78 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51260 $Y=23328 $D=1
M79 VSS 35 35 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51260 $Y=44928 $D=1
M80 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51476 $Y=23328 $D=1
M81 71 28 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=51476 $Y=45036 $D=1
M82 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51692 $Y=23328 $D=1
M83 VSS 28 71 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=51692 $Y=45036 $D=1
M84 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51908 $Y=23328 $D=1
M85 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52124 $Y=23328 $D=1
M86 691 36 74 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52124 $Y=51948 $D=1
M87 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52340 $Y=23328 $D=1
M88 VSS 38 691 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52340 $Y=51948 $D=1
M89 VSS 37 39 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52556 $Y=21708 $D=1
M90 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52556 $Y=23328 $D=1
M91 692 38 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52556 $Y=51948 $D=1
M92 73 39 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52772 $Y=21708 $D=1
M93 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52772 $Y=23328 $D=1
M94 74 36 692 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52772 $Y=51948 $D=1
M95 VSS 39 73 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52988 $Y=21708 $D=1
M96 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52988 $Y=23328 $D=1
M97 693 40 74 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52988 $Y=51948 $D=1
M98 73 39 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53204 $Y=21708 $D=1
M99 69 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53204 $Y=23328 $D=1
M100 VSS 41 693 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53204 $Y=51948 $D=1
M101 VSS 30 69 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53420 $Y=23328 $D=1
M102 694 41 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53420 $Y=51948 $D=1
M103 74 40 694 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53636 $Y=51948 $D=1
M104 VDD 3 44 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=14756 $Y=42228 $D=0
M105 44 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=14972 $Y=42228 $D=0
M106 VDD 4 44 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=15188 $Y=42228 $D=0
M107 44 4 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=15404 $Y=42228 $D=0
M108 45 5 44 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=15620 $Y=42228 $D=0
M109 44 5 45 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=15836 $Y=42228 $D=0
M110 45 6 44 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=16052 $Y=42228 $D=0
M111 44 6 45 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=16268 $Y=42228 $D=0
M112 47 7 45 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=16916 $Y=42228 $D=0
M113 45 7 47 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=17132 $Y=42228 $D=0
M114 21 8 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=24692 $Y=56808 $D=0
M115 VDD 8 21 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=24908 $Y=56808 $D=0
M116 19 9 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25124 $Y=40068 $D=0
M117 8 10 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25124 $Y=56808 $D=0
M118 VDD 11 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25340 $Y=56808 $D=0
M119 8 12 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25556 $Y=56808 $D=0
M120 VDD 13 19 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25772 $Y=40068 $D=0
M121 VDD 14 8 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=25772 $Y=56808 $D=0
M122 VDD 15 19 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=26420 $Y=40068 $D=0
M123 55 16 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=26636 $Y=48168 $D=0
M124 VDD 16 55 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=26852 $Y=48168 $D=0
M125 VDD 17 19 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27068 $Y=40068 $D=0
M126 55 16 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27068 $Y=48168 $D=0
M127 VDD 16 55 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27284 $Y=48168 $D=0
M128 55 16 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27500 $Y=48168 $D=0
M129 VDD 18 19 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27716 $Y=40068 $D=0
M130 VDD 16 55 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27716 $Y=48168 $D=0
M131 55 16 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=27932 $Y=48168 $D=0
M132 VDD 16 55 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=28148 $Y=48168 $D=0
M133 57 19 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=28364 $Y=40068 $D=0
M134 VDD 19 57 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=28580 $Y=40068 $D=0
M135 18 20 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30956 $Y=46548 $D=0
M136 VDD 20 18 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31172 $Y=46548 $D=0
M137 18 20 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31388 $Y=46548 $D=0
M138 VDD 20 18 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31604 $Y=46548 $D=0
M139 20 21 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31820 $Y=46548 $D=0
M140 VDD 22 20 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32684 $Y=46548 $D=0
M141 VDD 23 20 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33332 $Y=46548 $D=0
M142 61 24 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41540 $Y=37908 $D=0
M143 VDD 24 61 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41756 $Y=37908 $D=0
M144 61 24 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41972 $Y=37908 $D=0
M145 62 25 61 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42188 $Y=37908 $D=0
M146 61 25 62 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42404 $Y=37908 $D=0
M147 62 25 61 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42620 $Y=37908 $D=0
M148 64 26 62 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42836 $Y=37908 $D=0
M149 62 26 64 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43052 $Y=37908 $D=0
M150 64 26 62 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43268 $Y=37908 $D=0
M151 30 27 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47588 $Y=22788 $D=0
M152 71 28 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=47588 $Y=44388 $D=0
M153 VDD 27 30 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47804 $Y=22788 $D=0
M154 VDD 28 71 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=47804 $Y=44388 $D=0
M155 30 27 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48020 $Y=22788 $D=0
M156 31 29 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48020 $Y=44388 $D=0
M157 VDD 27 30 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48236 $Y=22788 $D=0
M158 VDD 29 31 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48236 $Y=44388 $D=0
M159 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48452 $Y=22788 $D=0
M160 31 31 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48452 $Y=44388 $D=0
M161 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48668 $Y=22788 $D=0
M162 VDD 31 31 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48668 $Y=44388 $D=0
M163 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48884 $Y=22788 $D=0
M164 71 28 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=48884 $Y=44388 $D=0
M165 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49100 $Y=22788 $D=0
M166 VDD 28 71 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=49100 $Y=44388 $D=0
M167 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49316 $Y=22788 $D=0
M168 33 32 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49316 $Y=44388 $D=0
M169 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49532 $Y=22788 $D=0
M170 VDD 32 33 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49532 $Y=44388 $D=0
M171 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49748 $Y=22788 $D=0
M172 33 33 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49748 $Y=44388 $D=0
M173 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49964 $Y=22788 $D=0
M174 VDD 33 33 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=49964 $Y=44388 $D=0
M175 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50180 $Y=22788 $D=0
M176 71 28 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=50180 $Y=44388 $D=0
M177 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50396 $Y=22788 $D=0
M178 VDD 28 71 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=50396 $Y=44388 $D=0
M179 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50612 $Y=22788 $D=0
M180 34 34 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50612 $Y=44388 $D=0
M181 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50828 $Y=22788 $D=0
M182 VDD 34 34 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=50828 $Y=44388 $D=0
M183 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51044 $Y=22788 $D=0
M184 34 35 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51044 $Y=44388 $D=0
M185 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51260 $Y=22788 $D=0
M186 VDD 35 34 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51260 $Y=44388 $D=0
M187 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51476 $Y=22788 $D=0
M188 71 28 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=51476 $Y=44388 $D=0
M189 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51692 $Y=22788 $D=0
M190 VDD 28 71 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=51692 $Y=44388 $D=0
M191 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=51908 $Y=22788 $D=0
M192 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52124 $Y=22788 $D=0
M193 VDD 36 72 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52124 $Y=52488 $D=0
M194 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52340 $Y=22788 $D=0
M195 72 38 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52340 $Y=52488 $D=0
M196 VDD 37 39 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52556 $Y=22248 $D=0
M197 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52556 $Y=22788 $D=0
M198 VDD 38 72 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52556 $Y=52488 $D=0
M199 73 39 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52772 $Y=22248 $D=0
M200 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52772 $Y=22788 $D=0
M201 72 36 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52772 $Y=52488 $D=0
M202 VDD 39 73 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52988 $Y=22248 $D=0
M203 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52988 $Y=22788 $D=0
M204 74 40 72 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=52988 $Y=52488 $D=0
M205 73 39 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53204 $Y=22248 $D=0
M206 69 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53204 $Y=22788 $D=0
M207 72 41 74 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53204 $Y=52488 $D=0
M208 VDD 30 69 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53420 $Y=22788 $D=0
M209 74 41 72 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53420 $Y=52488 $D=0
M210 72 40 74 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=53636 $Y=52488 $D=0
X3253 VSS VDD DECAPx1_ASAP7_75t_R $T=4752 15120 1 0 $X=4752 $Y=13952
X3254 VSS VDD DECAPx1_ASAP7_75t_R $T=4752 15120 0 0 $X=4752 $Y=15032
X3255 VSS VDD DECAPx1_ASAP7_75t_R $T=4752 19440 0 0 $X=4752 $Y=19352
X3256 VSS VDD DECAPx1_ASAP7_75t_R $T=10584 12960 0 0 $X=10584 $Y=12872
X3257 VSS VDD DECAPx1_ASAP7_75t_R $T=12744 12960 0 0 $X=12744 $Y=12872
X3258 VSS VDD DECAPx1_ASAP7_75t_R $T=13608 17280 1 0 $X=13608 $Y=16112
X3259 VSS VDD DECAPx1_ASAP7_75t_R $T=14472 19440 0 0 $X=14472 $Y=19352
X3260 VSS VDD DECAPx1_ASAP7_75t_R $T=19008 54000 1 0 $X=19008 $Y=52832
X3261 VSS VDD DECAPx1_ASAP7_75t_R $T=22032 45360 0 0 $X=22032 $Y=45272
X3262 VSS VDD DECAPx1_ASAP7_75t_R $T=24408 30240 0 0 $X=24408 $Y=30152
X3263 VSS VDD DECAPx1_ASAP7_75t_R $T=31536 34560 0 0 $X=31536 $Y=34472
X3264 VSS VDD DECAPx1_ASAP7_75t_R $T=33264 66960 1 0 $X=33264 $Y=65792
X3265 VSS VDD DECAPx1_ASAP7_75t_R $T=33480 56160 0 0 $X=33480 $Y=56072
X3266 VSS VDD DECAPx1_ASAP7_75t_R $T=35208 66960 1 0 $X=35208 $Y=65792
X3267 VSS VDD DECAPx1_ASAP7_75t_R $T=35424 36720 0 0 $X=35424 $Y=36632
X3268 VSS VDD DECAPx1_ASAP7_75t_R $T=35856 49680 1 0 $X=35856 $Y=48512
X3269 VSS VDD DECAPx1_ASAP7_75t_R $T=36720 60480 0 0 $X=36720 $Y=60392
X3270 VSS VDD DECAPx1_ASAP7_75t_R $T=38880 54000 0 0 $X=38880 $Y=53912
X3271 VSS VDD DECAPx1_ASAP7_75t_R $T=39096 49680 1 0 $X=39096 $Y=48512
X3272 VSS VDD DECAPx1_ASAP7_75t_R $T=46224 12960 0 0 $X=46224 $Y=12872
X3273 VSS VDD DECAPx1_ASAP7_75t_R $T=46224 30240 1 0 $X=46224 $Y=29072
X3274 VSS VDD DECAPx1_ASAP7_75t_R $T=50328 15120 0 0 $X=50328 $Y=15032
X3275 VSS VDD DECAPx1_ASAP7_75t_R $T=51408 25920 0 0 $X=51408 $Y=25832
X3276 VSS VDD DECAPx1_ASAP7_75t_R $T=53352 45360 1 0 $X=53352 $Y=44192
X3277 VSS VDD DECAPx1_ASAP7_75t_R $T=58320 23760 0 0 $X=58320 $Y=23672
X3278 VSS VDD DECAPx1_ASAP7_75t_R $T=58320 45360 1 0 $X=58320 $Y=44192
X3279 VSS VDD DECAPx1_ASAP7_75t_R $T=58752 15120 1 0 $X=58752 $Y=13952
X3280 VSS VDD DECAPx1_ASAP7_75t_R $T=58752 19440 0 0 $X=58752 $Y=19352
X3281 VSS VDD DECAPx1_ASAP7_75t_R $T=59616 58320 1 0 $X=59616 $Y=57152
X3282 VSS VDD DECAPx1_ASAP7_75t_R $T=67824 15120 0 0 $X=67824 $Y=15032
X3283 VSS VDD DECAPx1_ASAP7_75t_R $T=67824 58320 0 0 $X=67824 $Y=58232
X3284 VSS VDD 97 83 INVx1_ASAP7_75t_R $T=6696 28080 1 0 $X=6696 $Y=26912
X3285 VSS VDD 121 82 INVx1_ASAP7_75t_R $T=9936 34560 1 0 $X=9936 $Y=33392
X3286 VSS VDD 123 124 INVx1_ASAP7_75t_R $T=10152 30240 1 0 $X=10152 $Y=29072
X3287 VSS VDD 129 102 INVx1_ASAP7_75t_R $T=11232 47520 1 0 $X=11232 $Y=46352
X3288 VSS VDD 142 133 INVx1_ASAP7_75t_R $T=11880 34560 1 0 $X=11880 $Y=33392
X3289 VSS VDD 626 478 INVx1_ASAP7_75t_R $T=12096 32400 1 0 $X=12096 $Y=31232
X3290 VSS VDD 135 126 INVx1_ASAP7_75t_R $T=12528 49680 1 0 $X=12528 $Y=48512
X3291 VSS VDD 479 122 INVx1_ASAP7_75t_R $T=13608 28080 1 0 $X=13608 $Y=26912
X3292 VSS VDD 140 157 INVx1_ASAP7_75t_R $T=14256 34560 0 0 $X=14256 $Y=34472
X3293 VSS VDD 4 627 INVx1_ASAP7_75t_R $T=14688 41040 0 0 $X=14688 $Y=40952
X3294 VSS VDD 141 608 INVx1_ASAP7_75t_R $T=15336 32400 1 0 $X=15336 $Y=31232
X3295 VSS VDD 482 486 INVx1_ASAP7_75t_R $T=16632 41040 0 0 $X=16632 $Y=40952
X3296 VSS VDD 171 585 INVx1_ASAP7_75t_R $T=17280 32400 1 0 $X=17280 $Y=31232
X3297 VSS VDD 11 174 INVx1_ASAP7_75t_R $T=20088 58320 1 0 $X=20088 $Y=57152
X3298 VSS VDD 628 195 INVx1_ASAP7_75t_R $T=23760 34560 0 0 $X=23760 $Y=34472
X3299 VSS VDD 9 205 INVx1_ASAP7_75t_R $T=25272 38880 1 0 $X=25272 $Y=37712
X3300 VSS VDD 203 629 INVx1_ASAP7_75t_R $T=25704 47520 0 0 $X=25704 $Y=47432
X3301 VSS VDD 21 630 INVx1_ASAP7_75t_R $T=27216 56160 0 0 $X=27216 $Y=56072
X3302 VSS VDD 226 229 INVx1_ASAP7_75t_R $T=31968 30240 0 0 $X=31968 $Y=30152
X3303 VSS VDD 297 507 INVx1_ASAP7_75t_R $T=35424 34560 0 0 $X=35424 $Y=34472
X3304 VSS VDD 251 281 INVx1_ASAP7_75t_R $T=37368 64800 0 0 $X=37368 $Y=64712
X3305 VSS VDD 225 282 INVx1_ASAP7_75t_R $T=38664 64800 1 0 $X=38664 $Y=63632
X3306 VSS VDD 534 612 INVx1_ASAP7_75t_R $T=39744 64800 1 0 $X=39744 $Y=63632
X3307 VSS VDD 305 529 INVx1_ASAP7_75t_R $T=43632 28080 1 0 $X=43632 $Y=26912
X3308 VSS VDD 296 319 INVx1_ASAP7_75t_R $T=43848 25920 1 0 $X=43848 $Y=24752
X3309 VSS VDD 312 531 INVx1_ASAP7_75t_R $T=44496 8640 0 0 $X=44496 $Y=8552
X3310 VSS VDD 320 313 INVx1_ASAP7_75t_R $T=45576 8640 1 0 $X=45576 $Y=7472
X3311 VSS VDD 333 337 INVx1_ASAP7_75t_R $T=47088 28080 1 0 $X=47088 $Y=26912
X3312 VSS VDD 343 338 INVx1_ASAP7_75t_R $T=47088 30240 1 0 $X=47088 $Y=29072
X3313 VSS VDD 596 349 INVx1_ASAP7_75t_R $T=48600 15120 1 0 $X=48600 $Y=13952
X3314 VSS VDD 549 615 INVx1_ASAP7_75t_R $T=50544 10800 0 0 $X=50544 $Y=10712
X3315 VSS VDD 7 37 INVx1_ASAP7_75t_R $T=50760 21600 1 0 $X=50760 $Y=20432
X3316 VSS VDD 545 351 INVx1_ASAP7_75t_R $T=50760 28080 1 0 $X=50760 $Y=26912
X3317 VSS VDD 370 358 INVx1_ASAP7_75t_R $T=54000 17280 1 0 $X=54000 $Y=16112
X3318 VSS VDD 321 391 INVx1_ASAP7_75t_R $T=54000 41040 0 0 $X=54000 $Y=40952
X3319 VSS VDD 383 556 INVx1_ASAP7_75t_R $T=55728 8640 1 0 $X=55728 $Y=7472
X3320 VSS VDD 402 619 INVx1_ASAP7_75t_R $T=57456 12960 1 0 $X=57456 $Y=11792
X3321 VSS VDD 436 563 INVx1_ASAP7_75t_R $T=58320 49680 1 0 $X=58320 $Y=48512
X3322 VSS VDD 406 422 INVx1_ASAP7_75t_R $T=59184 45360 1 0 $X=59184 $Y=44192
X3323 VSS VDD 421 429 INVx1_ASAP7_75t_R $T=59832 25920 1 0 $X=59832 $Y=24752
X3324 VSS VDD 440 431 INVx1_ASAP7_75t_R $T=60480 12960 1 0 $X=60480 $Y=11792
X3325 VSS VDD 430 569 INVx1_ASAP7_75t_R $T=60696 21600 1 0 $X=60696 $Y=20432
X3326 VSS VDD 448 420 INVx1_ASAP7_75t_R $T=62424 23760 1 0 $X=62424 $Y=22592
X3327 VSS VDD 434 631 INVx1_ASAP7_75t_R $T=62640 30240 1 0 $X=62640 $Y=29072
X3328 VSS VDD 424 623 INVx1_ASAP7_75t_R $T=62640 45360 1 0 $X=62640 $Y=44192
X3329 VSS VDD 475 575 INVx1_ASAP7_75t_R $T=63720 49680 0 0 $X=63720 $Y=49592
X3330 VSS VDD 397 632 INVx1_ASAP7_75t_R $T=64368 19440 1 0 $X=64368 $Y=18272
X3331 VSS VDD 444 472 INVx1_ASAP7_75t_R $T=66312 12960 1 0 $X=66312 $Y=11792
X3332 VSS VDD 467 470 INVx1_ASAP7_75t_R $T=66744 30240 1 0 $X=66744 $Y=29072
X3333 VSS VDD 577 473 INVx1_ASAP7_75t_R $T=66960 23760 1 0 $X=66960 $Y=22592
X3334 VSS VDD 469 580 INVx1_ASAP7_75t_R $T=67176 15120 0 0 $X=67176 $Y=15032
X3335 VSS VDD 474 625 INVx1_ASAP7_75t_R $T=67824 30240 0 0 $X=67824 $Y=30152
X3353 VSS VDD ICV_9 $T=6480 17280 1 0 $X=6480 $Y=16112
X3354 VSS VDD ICV_9 $T=12528 15120 1 0 $X=12528 $Y=13952
X3355 VSS VDD ICV_9 $T=12528 38880 0 0 $X=12528 $Y=38792
X3356 VSS VDD ICV_9 $T=14256 45360 1 0 $X=14256 $Y=44192
X3357 VSS VDD ICV_9 $T=19008 58320 1 0 $X=19008 $Y=57152
X3358 VSS VDD ICV_9 $T=23976 54000 1 0 $X=23976 $Y=52832
X3359 VSS VDD ICV_9 $T=26136 56160 0 0 $X=26136 $Y=56072
X3360 VSS VDD ICV_9 $T=27864 49680 1 0 $X=27864 $Y=48512
X3361 VSS VDD ICV_9 $T=29592 47520 1 0 $X=29592 $Y=46352
X3362 VSS VDD ICV_9 $T=31752 54000 0 0 $X=31752 $Y=53912
X3363 VSS VDD ICV_9 $T=33912 58320 0 0 $X=33912 $Y=58232
X3364 VSS VDD ICV_9 $T=35640 45360 1 0 $X=35640 $Y=44192
X3365 VSS VDD ICV_9 $T=38016 23760 0 0 $X=38016 $Y=23672
X3366 VSS VDD ICV_9 $T=39096 56160 1 0 $X=39096 $Y=54992
X3367 VSS VDD ICV_9 $T=41688 36720 0 0 $X=41688 $Y=36632
X3368 VSS VDD ICV_9 $T=43848 30240 1 0 $X=43848 $Y=29072
X3369 VSS VDD ICV_9 $T=44712 28080 0 0 $X=44712 $Y=27992
X3370 VSS VDD ICV_9 $T=46224 45360 0 0 $X=46224 $Y=45272
X3371 VSS VDD ICV_9 $T=46656 47520 1 0 $X=46656 $Y=46352
X3372 VSS VDD ICV_9 $T=47952 41040 1 0 $X=47952 $Y=39872
X3373 VSS VDD ICV_9 $T=47952 58320 1 0 $X=47952 $Y=57152
X3374 VSS VDD ICV_9 $T=48384 25920 1 0 $X=48384 $Y=24752
X3375 VSS VDD ICV_9 $T=48600 36720 0 0 $X=48600 $Y=36632
X3376 VSS VDD ICV_9 $T=51408 36720 0 0 $X=51408 $Y=36632
X3377 VSS VDD ICV_9 $T=54000 36720 0 0 $X=54000 $Y=36632
X3378 VSS VDD ICV_9 $T=54648 43200 0 0 $X=54648 $Y=43112
X3379 VSS VDD ICV_9 $T=55296 43200 1 0 $X=55296 $Y=42032
X3380 VSS VDD ICV_9 $T=55512 12960 0 0 $X=55512 $Y=12872
X3381 VSS VDD ICV_9 $T=67608 36720 0 0 $X=67608 $Y=36632
X3382 VSS VDD ICV_9 $T=67608 62640 1 0 $X=67608 $Y=61472
X3383 VSS VDD ICV_9 $T=67608 66960 1 0 $X=67608 $Y=65792
X3384 VSS VDD 82 s_axis_tready BUFx2_ASAP7_75t_R $T=4752 34560 1 0 $X=4752 $Y=33392
X3385 VSS VDD s_axis_tdata[4] 635 BUFx2_ASAP7_75t_R $T=5184 12960 0 0 $X=5184 $Y=12872
X3386 VSS VDD s_axis_tdata[5] 90 BUFx2_ASAP7_75t_R $T=5400 17280 1 0 $X=5400 $Y=16112
X3387 VSS VDD s_axis_tdata[2] 93 BUFx2_ASAP7_75t_R $T=5616 15120 0 0 $X=5616 $Y=15032
X3388 VSS VDD s_axis_tdata[6] 94 BUFx2_ASAP7_75t_R $T=5616 19440 0 0 $X=5616 $Y=19352
X3389 VSS VDD s_axis_tvalid 142 BUFx2_ASAP7_75t_R $T=6264 34560 1 0 $X=6264 $Y=33392
X3390 VSS VDD s_axis_tdata[7] 101 BUFx2_ASAP7_75t_R $T=6480 23760 0 0 $X=6480 $Y=23672
X3391 VSS VDD rst 7 BUFx2_ASAP7_75t_R $T=6696 47520 0 0 $X=6696 $Y=47432
X3392 VSS VDD s_axis_tdata[0] 105 BUFx2_ASAP7_75t_R $T=7128 21600 0 0 $X=7128 $Y=21512
X3393 VSS VDD 102 tx_busy BUFx2_ASAP7_75t_R $T=7344 47520 1 0 $X=7344 $Y=46352
X3394 VSS VDD prescale[7] 254 BUFx2_ASAP7_75t_R $T=32184 64800 0 0 $X=32184 $Y=64712
X3395 VSS VDD prescale[5] 511 BUFx2_ASAP7_75t_R $T=33264 64800 0 0 $X=33264 $Y=64712
X3396 VSS VDD prescale[0] 225 BUFx2_ASAP7_75t_R $T=34128 66960 1 0 $X=34128 $Y=65792
X3397 VSS VDD prescale[1] 251 BUFx2_ASAP7_75t_R $T=34344 64800 0 0 $X=34344 $Y=64712
X3398 VSS VDD prescale[3] 237 BUFx2_ASAP7_75t_R $T=35424 69120 1 0 $X=35424 $Y=67952
X3399 VSS VDD prescale[2] 268 BUFx2_ASAP7_75t_R $T=36288 66960 0 0 $X=36288 $Y=66872
X3400 VSS VDD prescale[9] 521 BUFx2_ASAP7_75t_R $T=38664 66960 1 0 $X=38664 $Y=65792
X3401 VSS VDD prescale[8] 245 BUFx2_ASAP7_75t_R $T=38880 69120 1 0 $X=38880 $Y=67952
X3402 VSS VDD prescale[6] 259 BUFx2_ASAP7_75t_R $T=39312 66960 0 0 $X=39312 $Y=66872
X3403 VSS VDD prescale[10] 290 BUFx2_ASAP7_75t_R $T=39744 66960 1 0 $X=39744 $Y=65792
X3404 VSS VDD prescale[11] 613 BUFx2_ASAP7_75t_R $T=40176 64800 0 0 $X=40176 $Y=64712
X3405 VSS VDD 313 m_axis_tdata[5] BUFx2_ASAP7_75t_R $T=44496 6480 1 0 $X=44496 $Y=5312
X3406 VSS VDD 531 m_axis_tdata[6] BUFx2_ASAP7_75t_R $T=44712 6480 0 0 $X=44712 $Y=6392
X3407 VSS VDD 556 m_axis_tdata[4] BUFx2_ASAP7_75t_R $T=55944 6480 0 0 $X=55944 $Y=6392
X3408 VSS VDD prescale[13] 288 BUFx2_ASAP7_75t_R $T=62208 36720 1 0 $X=62208 $Y=35552
X3409 VSS VDD 429 rx_busy BUFx2_ASAP7_75t_R $T=63288 25920 1 0 $X=63288 $Y=24752
X3410 VSS VDD prescale[15] 261 BUFx2_ASAP7_75t_R $T=65448 36720 0 0 $X=65448 $Y=36632
X3411 VSS VDD 632 m_axis_tdata[7] BUFx2_ASAP7_75t_R $T=65880 19440 0 0 $X=65880 $Y=19352
X3412 VSS VDD prescale[14] 26 BUFx2_ASAP7_75t_R $T=66528 36720 0 0 $X=66528 $Y=36632
X3413 VSS VDD 472 m_axis_tdata[3] BUFx2_ASAP7_75t_R $T=67608 12960 1 0 $X=67608 $Y=11792
X3414 VSS VDD 580 m_axis_tdata[2] BUFx2_ASAP7_75t_R $T=67608 15120 1 0 $X=67608 $Y=13952
X3415 VSS VDD 471 m_axis_tdata[1] BUFx2_ASAP7_75t_R $T=67608 21600 1 0 $X=67608 $Y=20432
X3416 VSS VDD 473 m_axis_tdata[0] BUFx2_ASAP7_75t_R $T=67608 23760 1 0 $X=67608 $Y=22592
X3417 VSS VDD 625 rx_overrun_error BUFx2_ASAP7_75t_R $T=67608 28080 0 0 $X=67608 $Y=27992
X3418 VSS VDD DECAPx2_ASAP7_75t_R $T=12528 12960 1 0 $X=12528 $Y=11792
X3419 VSS VDD DECAPx2_ASAP7_75t_R $T=18576 51840 0 0 $X=18576 $Y=51752
X3420 VSS VDD DECAPx2_ASAP7_75t_R $T=21168 51840 0 0 $X=21168 $Y=51752
X3421 VSS VDD DECAPx2_ASAP7_75t_R $T=22896 28080 1 0 $X=22896 $Y=26912
X3422 VSS VDD DECAPx2_ASAP7_75t_R $T=26784 28080 0 0 $X=26784 $Y=27992
X3423 VSS VDD DECAPx2_ASAP7_75t_R $T=37368 66960 1 0 $X=37368 $Y=65792
X3424 VSS VDD DECAPx2_ASAP7_75t_R $T=37584 43200 0 0 $X=37584 $Y=43112
X3425 VSS VDD DECAPx2_ASAP7_75t_R $T=41040 10800 0 0 $X=41040 $Y=10712
X3426 VSS VDD DECAPx2_ASAP7_75t_R $T=43632 12960 1 0 $X=43632 $Y=11792
X3427 VSS VDD DECAPx2_ASAP7_75t_R $T=43848 56160 1 0 $X=43848 $Y=54992
X3428 VSS VDD DECAPx2_ASAP7_75t_R $T=44928 23760 0 0 $X=44928 $Y=23672
X3429 VSS VDD DECAPx2_ASAP7_75t_R $T=45576 49680 1 0 $X=45576 $Y=48512
X3430 VSS VDD DECAPx2_ASAP7_75t_R $T=48816 25920 0 0 $X=48816 $Y=25832
X3431 VSS VDD DECAPx2_ASAP7_75t_R $T=48816 43200 0 0 $X=48816 $Y=43112
X3432 VSS VDD DECAPx2_ASAP7_75t_R $T=49680 38880 0 0 $X=49680 $Y=38792
X3433 VSS VDD DECAPx2_ASAP7_75t_R $T=50760 25920 1 0 $X=50760 $Y=24752
X3434 VSS VDD DECAPx2_ASAP7_75t_R $T=50976 47520 0 0 $X=50976 $Y=47432
X3435 VSS VDD DECAPx2_ASAP7_75t_R $T=52704 41040 0 0 $X=52704 $Y=40952
X3436 VSS VDD DECAPx2_ASAP7_75t_R $T=53352 60480 1 0 $X=53352 $Y=59312
X3437 VSS VDD DECAPx2_ASAP7_75t_R $T=57240 38880 1 0 $X=57240 $Y=37712
X3438 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 54000 1 0 $X=58752 $Y=52832
X3439 VSS VDD DECAPx2_ASAP7_75t_R $T=59616 56160 0 0 $X=59616 $Y=56072
X3440 VSS VDD DECAPx2_ASAP7_75t_R $T=64152 36720 0 0 $X=64152 $Y=36632
X3441 VSS VDD DECAPx2_ASAP7_75t_R $T=64584 19440 0 0 $X=64584 $Y=19352
X3442 VSS VDD DECAPx2_ASAP7_75t_R $T=67176 10800 1 0 $X=67176 $Y=9632
X3443 VSS VDD DECAPx2_ASAP7_75t_R $T=67176 38880 1 0 $X=67176 $Y=37712
X3444 VSS VDD DECAPx2_ASAP7_75t_R $T=67176 62640 0 0 $X=67176 $Y=62552
X3445 VSS VDD DECAPx2_ASAP7_75t_R $T=67176 64800 1 0 $X=67176 $Y=63632
X3446 VSS VDD DECAPx2_ASAP7_75t_R $T=67176 66960 0 0 $X=67176 $Y=66872
X3447 VSS VDD DECAPx2_ASAP7_75t_R $T=67392 54000 1 0 $X=67392 $Y=52832
X3448 VSS VDD DECAPx2_ASAP7_75t_R $T=67392 56160 0 0 $X=67392 $Y=56072
X3449 VSS VDD 83 txd ICV_10 $T=4752 28080 1 0 $X=4752 $Y=26912
X3450 VSS VDD s_axis_tdata[3] 100 ICV_10 $T=6264 12960 0 0 $X=6264 $Y=12872
X3451 VSS VDD s_axis_tdata[1] 131 ICV_10 $T=10800 19440 1 0 $X=10800 $Y=18272
X3452 VSS VDD prescale[4] 253 ICV_10 $T=36072 66960 1 0 $X=36072 $Y=65792
X3453 VSS VDD rxd 285 ICV_10 $T=37800 17280 1 0 $X=37800 $Y=16112
X3454 VSS VDD prescale[12] 306 ICV_10 $T=61128 34560 0 0 $X=61128 $Y=34472
X3455 VSS VDD 458 rx_frame_error ICV_10 $T=64368 25920 0 0 $X=64368 $Y=25832
X3456 VSS VDD m_axis_tready 461 ICV_10 $T=65448 30240 1 0 $X=65448 $Y=29072
X3457 VSS VDD 470 m_axis_tvalid ICV_10 $T=67392 30240 1 0 $X=67392 $Y=29072
X3589 VSS VDD ICV_13 $T=4752 23760 0 0 $X=4752 $Y=23672
X3590 VSS VDD ICV_13 $T=9504 30240 0 0 $X=9504 $Y=30152
X3591 VSS VDD ICV_13 $T=12744 32400 0 0 $X=12744 $Y=32312
X3592 VSS VDD ICV_13 $T=13608 47520 1 0 $X=13608 $Y=46352
X3593 VSS VDD ICV_13 $T=19224 49680 1 0 $X=19224 $Y=48512
X3594 VSS VDD ICV_13 $T=22032 34560 0 0 $X=22032 $Y=34472
X3595 VSS VDD ICV_13 $T=25920 32400 0 0 $X=25920 $Y=32312
X3596 VSS VDD ICV_13 $T=29160 34560 1 0 $X=29160 $Y=33392
X3597 VSS VDD ICV_13 $T=37368 28080 0 0 $X=37368 $Y=27992
X3598 VSS VDD ICV_13 $T=40176 25920 1 0 $X=40176 $Y=24752
X3599 VSS VDD ICV_13 $T=42768 6480 1 0 $X=42768 $Y=5312
X3600 VSS VDD ICV_13 $T=42768 8640 0 0 $X=42768 $Y=8552
X3601 VSS VDD ICV_13 $T=52056 34560 0 0 $X=52056 $Y=34472
X3602 VSS VDD ICV_13 $T=54432 56160 1 0 $X=54432 $Y=54992
X3603 VSS VDD ICV_13 $T=57240 32400 0 0 $X=57240 $Y=32312
X3604 VSS VDD ICV_13 $T=60480 36720 1 0 $X=60480 $Y=35552
X3605 VSS VDD ICV_13 $T=66960 17280 0 0 $X=66960 $Y=17192
X3606 VSS VDD ICV_13 $T=66960 19440 0 0 $X=66960 $Y=19352
X3607 VSS VDD ICV_13 $T=66960 56160 1 0 $X=66960 $Y=54992
X3608 VSS VDD 201 652 ICV_14 $T=25056 32400 0 0 $X=25056 $Y=32312
X3609 VSS VDD 23 653 ICV_14 $T=27864 56160 0 0 $X=27864 $Y=56072
X3610 VSS VDD 22 589 ICV_14 $T=28944 49680 0 0 $X=28944 $Y=49592
X3611 VSS VDD 221 498 ICV_14 $T=30672 34560 0 0 $X=30672 $Y=34472
X3612 VSS VDD 428 425 ICV_14 $T=61560 15120 0 0 $X=61560 $Y=15032
X3613 VSS VDD 441 443 ICV_14 $T=62208 41040 1 0 $X=62208 $Y=39872
X3614 VSS VDD 417 458 ICV_14 $T=63504 25920 0 0 $X=63504 $Y=25832
X3615 VSS VDD 447 471 ICV_14 $T=66744 21600 1 0 $X=66744 $Y=20432
X3616 VSS VDD DECAPx4_ASAP7_75t_R $T=4752 32400 1 0 $X=4752 $Y=31232
X3617 VSS VDD DECAPx4_ASAP7_75t_R $T=7776 47520 0 0 $X=7776 $Y=47432
X3618 VSS VDD DECAPx4_ASAP7_75t_R $T=20520 30240 1 0 $X=20520 $Y=29072
X3619 VSS VDD DECAPx4_ASAP7_75t_R $T=24624 45360 0 0 $X=24624 $Y=45272
X3620 VSS VDD DECAPx4_ASAP7_75t_R $T=31104 45360 1 0 $X=31104 $Y=44192
X3621 VSS VDD DECAPx4_ASAP7_75t_R $T=32616 36720 1 0 $X=32616 $Y=35552
X3622 VSS VDD DECAPx4_ASAP7_75t_R $T=33264 60480 0 0 $X=33264 $Y=60392
X3623 VSS VDD DECAPx4_ASAP7_75t_R $T=33264 69120 1 0 $X=33264 $Y=67952
X3624 VSS VDD DECAPx4_ASAP7_75t_R $T=35640 23760 1 0 $X=35640 $Y=22592
X3625 VSS VDD DECAPx4_ASAP7_75t_R $T=35640 56160 1 0 $X=35640 $Y=54992
X3626 VSS VDD DECAPx4_ASAP7_75t_R $T=38016 64800 0 0 $X=38016 $Y=64712
X3627 VSS VDD DECAPx4_ASAP7_75t_R $T=43848 21600 0 0 $X=43848 $Y=21512
X3628 VSS VDD DECAPx4_ASAP7_75t_R $T=45144 45360 1 0 $X=45144 $Y=44192
X3629 VSS VDD DECAPx4_ASAP7_75t_R $T=48384 10800 0 0 $X=48384 $Y=10712
X3630 VSS VDD DECAPx4_ASAP7_75t_R $T=49464 49680 0 0 $X=49464 $Y=49592
X3631 VSS VDD DECAPx4_ASAP7_75t_R $T=52488 28080 0 0 $X=52488 $Y=27992
X3632 VSS VDD DECAPx4_ASAP7_75t_R $T=60048 41040 1 0 $X=60048 $Y=39872
X3633 VSS VDD DECAPx4_ASAP7_75t_R $T=64800 23760 1 0 $X=64800 $Y=22592
X3634 VSS VDD DECAPx4_ASAP7_75t_R $T=66528 6480 0 0 $X=66528 $Y=6392
X3635 VSS VDD DECAPx4_ASAP7_75t_R $T=66528 54000 0 0 $X=66528 $Y=53912
X3636 VSS VDD DECAPx4_ASAP7_75t_R $T=66528 60480 0 0 $X=66528 $Y=60392
X3879 VSS VDD ICV_18 $T=8424 47520 1 0 $X=8424 $Y=46352
X3880 VSS VDD ICV_18 $T=12312 23760 1 0 $X=12312 $Y=22592
X3881 VSS VDD ICV_18 $T=29808 30240 1 0 $X=29808 $Y=29072
X3882 VSS VDD ICV_18 $T=31752 38880 1 0 $X=31752 $Y=37712
X3883 VSS VDD ICV_18 $T=35640 47520 0 0 $X=35640 $Y=47432
X3884 VSS VDD ICV_18 $T=36720 38880 0 0 $X=36720 $Y=38792
X3885 VSS VDD ICV_18 $T=42768 8640 1 0 $X=42768 $Y=7472
X3886 VSS VDD ICV_18 $T=44280 28080 1 0 $X=44280 $Y=26912
X3887 VSS VDD ICV_18 $T=51192 10800 0 0 $X=51192 $Y=10712
X3888 VSS VDD ICV_18 $T=51192 17280 0 0 $X=51192 $Y=17192
X3889 VSS VDD ICV_18 $T=59832 45360 0 0 $X=59832 $Y=45272
X3890 VSS VDD ICV_18 $T=61560 19440 1 0 $X=61560 $Y=18272
X3891 VSS VDD ICV_18 $T=65880 8640 1 0 $X=65880 $Y=7472
X3892 VSS VDD ICV_18 $T=65880 10800 0 0 $X=65880 $Y=10712
X3991 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 12960 1 0 $X=4752 $Y=11792
X3992 VSS VDD DECAPx6_ASAP7_75t_R $T=10584 15120 0 0 $X=10584 $Y=15032
X3993 VSS VDD DECAPx6_ASAP7_75t_R $T=11016 21600 0 0 $X=11016 $Y=21512
X3994 VSS VDD DECAPx6_ASAP7_75t_R $T=12744 30240 0 0 $X=12744 $Y=30152
X3995 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 51840 0 0 $X=14256 $Y=51752
X3996 VSS VDD DECAPx6_ASAP7_75t_R $T=26352 43200 0 0 $X=26352 $Y=43112
X3997 VSS VDD DECAPx6_ASAP7_75t_R $T=27432 38880 1 0 $X=27432 $Y=37712
X3998 VSS VDD DECAPx6_ASAP7_75t_R $T=33264 66960 0 0 $X=33264 $Y=66872
X3999 VSS VDD DECAPx6_ASAP7_75t_R $T=38016 21600 1 0 $X=38016 $Y=20432
X4000 VSS VDD DECAPx6_ASAP7_75t_R $T=41472 54000 0 0 $X=41472 $Y=53912
X4001 VSS VDD DECAPx6_ASAP7_75t_R $T=41904 54000 1 0 $X=41904 $Y=52832
X4002 VSS VDD DECAPx6_ASAP7_75t_R $T=42120 23760 1 0 $X=42120 $Y=22592
X4003 VSS VDD DECAPx6_ASAP7_75t_R $T=43200 43200 0 0 $X=43200 $Y=43112
X4004 VSS VDD DECAPx6_ASAP7_75t_R $T=47736 36720 1 0 $X=47736 $Y=35552
X4005 VSS VDD DECAPx6_ASAP7_75t_R $T=48816 12960 0 0 $X=48816 $Y=12872
X4006 VSS VDD DECAPx6_ASAP7_75t_R $T=51624 19440 1 0 $X=51624 $Y=18272
X4007 VSS VDD DECAPx6_ASAP7_75t_R $T=53136 51840 1 0 $X=53136 $Y=50672
X4008 VSS VDD DECAPx6_ASAP7_75t_R $T=54648 47520 0 0 $X=54648 $Y=47432
X4009 VSS VDD DECAPx6_ASAP7_75t_R $T=58104 34560 0 0 $X=58104 $Y=34472
X4010 VSS VDD DECAPx6_ASAP7_75t_R $T=58104 43200 0 0 $X=58104 $Y=43112
X4011 VSS VDD DECAPx6_ASAP7_75t_R $T=59184 47520 1 0 $X=59184 $Y=46352
X4012 VSS VDD DECAPx6_ASAP7_75t_R $T=59400 54000 0 0 $X=59400 $Y=53912
X4013 VSS VDD DECAPx6_ASAP7_75t_R $T=60480 32400 1 0 $X=60480 $Y=31232
X4014 VSS VDD DECAPx6_ASAP7_75t_R $T=60480 49680 1 0 $X=60480 $Y=48512
X4015 VSS VDD DECAPx6_ASAP7_75t_R $T=65448 43200 1 0 $X=65448 $Y=42032
X4016 VSS VDD DECAPx6_ASAP7_75t_R $T=65448 43200 0 0 $X=65448 $Y=43112
X4017 VSS VDD DECAPx6_ASAP7_75t_R $T=65664 21600 0 0 $X=65664 $Y=21512
X4018 VSS VDD DECAPx6_ASAP7_75t_R $T=65664 25920 0 0 $X=65664 $Y=25832
X4019 VSS VDD DECAPx6_ASAP7_75t_R $T=65664 51840 0 0 $X=65664 $Y=51752
X4049 VSS VDD 627 172 157 57 660 AND4x1_ASAP7_75t_R $T=15336 38880 0 0 $X=15336 $Y=38730
X4050 VSS VDD 14 12 168 180 586 AND4x1_ASAP7_75t_R $T=20520 54000 0 0 $X=20520 $Y=53850
X4051 VSS VDD 187 210 203 17 178 AND4x1_ASAP7_75t_R $T=22896 43200 0 0 $X=22896 $Y=43050
X4052 VSS VDD 187 210 17 15 489 AND4x1_ASAP7_75t_R $T=23544 43200 1 0 $X=23544 $Y=42032
X4053 VSS VDD 14 12 11 489 208 AND4x1_ASAP7_75t_R $T=24408 56160 1 0 $X=24408 $Y=54992
X4054 VSS VDD 73 50 610 250 170 AND4x1_ASAP7_75t_R $T=24840 36720 1 0 $X=24840 $Y=35552
X4055 VSS VDD 18 668 13 9 273 AND4x1_ASAP7_75t_R $T=25704 38880 0 0 $X=25704 $Y=38730
X4056 VSS VDD 221 234 226 297 9 AND4x1_ASAP7_75t_R $T=32400 34560 0 0 $X=32400 $Y=34410
X4057 VSS VDD 499 223 293 304 17 AND4x1_ASAP7_75t_R $T=32832 41040 0 0 $X=32832 $Y=40890
X4058 VSS VDD 73 269 662 60 272 AND4x1_ASAP7_75t_R $T=36288 36720 1 0 $X=36288 $Y=35552
X4059 VSS VDD 37 367 353 371 423 AND4x1_ASAP7_75t_R $T=50760 21600 0 0 $X=50760 $Y=21450
X4060 VSS VDD 317 73 353 366 382 AND4x1_ASAP7_75t_R $T=52272 25920 0 0 $X=52272 $Y=25770
X4061 VSS VDD 385 555 618 377 399 AND4x1_ASAP7_75t_R $T=55080 36720 0 0 $X=55080 $Y=36570
X4062 VSS VDD 392 408 409 436 424 AND4x1_ASAP7_75t_R $T=57672 47520 1 0 $X=57672 $Y=46352
X4063 VSS VDD 408 409 563 407 419 AND4x1_ASAP7_75t_R $T=57672 47520 0 0 $X=57672 $Y=47370
X4064 VSS VDD 427 399 452 400 624 AND4x1_ASAP7_75t_R $T=62640 36720 0 0 $X=62640 $Y=36570
X4065 VSS VDD 424 414 435 456 394 AND4x1_ASAP7_75t_R $T=62640 45360 0 0 $X=62640 $Y=45210
X4066 VSS VDD 443 424 657 452 353 AND4x1_ASAP7_75t_R $T=63072 41040 1 0 $X=63072 $Y=39872
X4067 VSS VDD 475 460 455 561 457 AND4x1_ASAP7_75t_R $T=65016 54000 0 0 $X=65016 $Y=53850
X4068 VSS VDD 475 460 455 459 414 AND4x1_ASAP7_75t_R $T=65448 56160 1 0 $X=65448 $Y=54992
X4159 VSS VDD ICV_25 $T=4752 17280 0 0 $X=4752 $Y=17192
X4160 VSS VDD ICV_25 $T=10800 28080 0 0 $X=10800 $Y=27992
X4161 VSS VDD ICV_25 $T=19872 36720 0 0 $X=19872 $Y=36632
X4162 VSS VDD ICV_25 $T=33696 47520 1 0 $X=33696 $Y=46352
X4163 VSS VDD ICV_25 $T=38880 60480 1 0 $X=38880 $Y=59312
X4164 VSS VDD ICV_25 $T=55296 36720 1 0 $X=55296 $Y=35552
X4165 VSS VDD ICV_25 $T=65016 17280 1 0 $X=65016 $Y=16112
X4166 VSS VDD ICV_25 $T=65016 19440 1 0 $X=65016 $Y=18272
X4167 VSS VDD ICV_25 $T=65016 32400 1 0 $X=65016 $Y=31232
X4168 VSS VDD ICV_25 $T=65016 32400 0 0 $X=65016 $Y=32312
X4169 VSS VDD ICV_25 $T=65016 47520 1 0 $X=65016 $Y=46352
X4170 VSS VDD ICV_25 $T=65016 51840 1 0 $X=65016 $Y=50672
X4171 VSS VDD ICV_26 $T=17712 25920 1 0 $X=17712 $Y=24752
X4172 VSS VDD ICV_26 $T=18144 25920 0 0 $X=18144 $Y=25832
X4173 VSS VDD ICV_26 $T=26352 43200 1 0 $X=26352 $Y=42032
X4174 VSS VDD ICV_26 $T=35856 56160 0 0 $X=35856 $Y=56072
X4175 VSS VDD ICV_26 $T=44064 43200 1 0 $X=44064 $Y=42032
X4176 VSS VDD ICV_26 $T=47952 51840 0 0 $X=47952 $Y=51752
X4177 VSS VDD ICV_26 $T=59616 30240 0 0 $X=59616 $Y=30152
X4178 VSS VDD ICV_26 $T=64800 34560 1 0 $X=64800 $Y=33392
X4179 VSS VDD ICV_26 $T=64800 41040 0 0 $X=64800 $Y=40952
X4180 VSS VDD ICV_26 $T=64800 45360 1 0 $X=64800 $Y=44192
X4181 VSS VDD ICV_26 $T=64800 49680 1 0 $X=64800 $Y=48512
X4182 VSS VDD 166 178 673 XNOR2x2_ASAP7_75t_R $T=19656 45360 0 0 $X=19656 $Y=45106
X4183 VSS VDD 225 164 590 XNOR2x2_ASAP7_75t_R $T=30024 62640 0 0 $X=30024 $Y=62386
X4184 VSS VDD 245 241 249 XNOR2x2_ASAP7_75t_R $T=32832 54000 0 0 $X=32832 $Y=53746
X4185 VSS VDD 293 222 257 XNOR2x2_ASAP7_75t_R $T=33264 45360 1 0 $X=33264 $Y=44192
X4186 VSS VDD 234 227 255 XNOR2x2_ASAP7_75t_R $T=33696 32400 0 0 $X=33696 $Y=32146
X4187 VSS VDD 511 505 189 XNOR2x2_ASAP7_75t_R $T=33696 64800 1 0 $X=33696 $Y=63632
X4188 VSS VDD 253 247 508 XNOR2x2_ASAP7_75t_R $T=33912 51840 0 0 $X=33912 $Y=51586
X4189 VSS VDD 254 260 263 XNOR2x2_ASAP7_75t_R $T=34128 58320 1 0 $X=34128 $Y=57152
X4190 VSS VDD 261 256 264 XNOR2x2_ASAP7_75t_R $T=34560 38880 1 0 $X=34560 $Y=37712
X4191 VSS VDD 511 242 278 XNOR2x2_ASAP7_75t_R $T=36504 60480 1 0 $X=36504 $Y=59312
X4192 VSS VDD 611 268 286 XNOR2x2_ASAP7_75t_R $T=37584 60480 0 0 $X=37584 $Y=60226
X4193 VSS VDD 26 520 287 XNOR2x2_ASAP7_75t_R $T=37800 36720 0 0 $X=37800 $Y=36466
X4194 VSS VDD 288 674 675 XNOR2x2_ASAP7_75t_R $T=39312 36720 1 0 $X=39312 $Y=35552
X4195 VSS VDD 306 525 526 XNOR2x2_ASAP7_75t_R $T=39528 34560 1 0 $X=39528 $Y=33392
X4196 VSS VDD 259 252 298 XNOR2x2_ASAP7_75t_R $T=39744 56160 0 0 $X=39744 $Y=55906
X4197 VSS VDD 290 294 334 XNOR2x2_ASAP7_75t_R $T=41472 56160 1 0 $X=41472 $Y=54992
X4198 VSS VDD 254 24 543 XNOR2x2_ASAP7_75t_R $T=42120 56160 0 0 $X=42120 $Y=55906
X4199 VSS VDD 245 299 311 XNOR2x2_ASAP7_75t_R $T=42120 58320 1 0 $X=42120 $Y=57152
X4200 VSS VDD 306 295 316 XNOR2x2_ASAP7_75t_R $T=42768 45360 1 0 $X=42768 $Y=44192
X4201 VSS VDD 261 64 365 XNOR2x2_ASAP7_75t_R $T=44064 38880 1 0 $X=44064 $Y=37712
X4202 VSS VDD 385 394 354 XNOR2x2_ASAP7_75t_R $T=52272 47520 0 0 $X=52272 $Y=47266
X4203 VSS VDD 377 390 341 XNOR2x2_ASAP7_75t_R $T=53136 38880 1 0 $X=53136 $Y=37712
X4204 VSS VDD 618 633 396 XNOR2x2_ASAP7_75t_R $T=53784 34560 0 0 $X=53784 $Y=34306
X4205 VSS VDD 408 407 40 XNOR2x2_ASAP7_75t_R $T=55512 49680 1 0 $X=55512 $Y=48512
X4206 VSS VDD 410 558 389 XNOR2x2_ASAP7_75t_R $T=55944 56160 0 0 $X=55944 $Y=55906
X4207 VSS VDD 409 560 415 XNOR2x2_ASAP7_75t_R $T=56160 45360 0 0 $X=56160 $Y=45106
X4208 VSS VDD 412 562 666 XNOR2x2_ASAP7_75t_R $T=57240 58320 1 0 $X=57240 $Y=57152
X4209 VSS VDD 455 576 432 XNOR2x2_ASAP7_75t_R $T=62640 54000 0 0 $X=62640 $Y=53746
X4210 VSS VDD 459 457 462 XNOR2x2_ASAP7_75t_R $T=63072 56160 1 0 $X=63072 $Y=54992
X4211 VSS VDD 460 603 465 XNOR2x2_ASAP7_75t_R $T=63288 51840 0 0 $X=63288 $Y=51586
X4212 VSS VDD 446 624 468 XNOR2x2_ASAP7_75t_R $T=64800 38880 1 0 $X=64800 $Y=37712
X4272 VSS VDD ICV_29 $T=14256 47520 0 0 $X=14256 $Y=47432
X4273 VSS VDD ICV_29 $T=27432 32400 1 0 $X=27432 $Y=31232
X4274 VSS VDD ICV_29 $T=36288 28080 1 0 $X=36288 $Y=26912
X4275 VSS VDD ICV_29 $T=37368 25920 0 0 $X=37368 $Y=25832
X4276 VSS VDD ICV_29 $T=44712 30240 0 0 $X=44712 $Y=30152
X4277 VSS VDD ICV_29 $T=58968 56160 1 0 $X=58968 $Y=54992
X4278 VSS VDD ICV_29 $T=59616 51840 1 0 $X=59616 $Y=50672
X4279 VSS VDD ICV_29 $T=64584 6480 1 0 $X=64584 $Y=5312
X4280 VSS VDD ICV_29 $T=64584 8640 0 0 $X=64584 $Y=8552
X4281 VSS VDD ICV_29 $T=64584 41040 1 0 $X=64584 $Y=39872
X4282 VSS VDD ICV_30 $T=4752 21600 1 0 $X=4752 $Y=20432
X4283 VSS VDD ICV_30 $T=4752 47520 1 0 $X=4752 $Y=46352
X4284 VSS VDD ICV_30 $T=7344 34560 1 0 $X=7344 $Y=33392
X4285 VSS VDD ICV_30 $T=12744 32400 1 0 $X=12744 $Y=31232
X4286 VSS VDD ICV_30 $T=20736 41040 1 0 $X=20736 $Y=39872
X4287 VSS VDD ICV_30 $T=27864 36720 1 0 $X=27864 $Y=35552
X4288 VSS VDD ICV_30 $T=28944 54000 1 0 $X=28944 $Y=52832
X4289 VSS VDD ICV_30 $T=30456 56160 1 0 $X=30456 $Y=54992
X4290 VSS VDD ICV_30 $T=33264 45360 0 0 $X=33264 $Y=45272
X4291 VSS VDD ICV_30 $T=42336 51840 0 0 $X=42336 $Y=51752
X4292 VSS VDD ICV_30 $T=42768 25920 0 0 $X=42768 $Y=25832
X4293 VSS VDD ICV_30 $T=48600 41040 0 0 $X=48600 $Y=40952
X4294 VSS VDD ICV_30 $T=60264 38880 0 0 $X=60264 $Y=38792
X4295 VSS VDD ICV_30 $T=60912 34560 1 0 $X=60912 $Y=33392
X4296 VSS VDD ICV_30 $T=64584 15120 0 0 $X=64584 $Y=15032
X4327 VSS VDD ICV_32 $T=36504 58320 1 0 $X=36504 $Y=57152
X4328 VSS VDD ICV_32 $T=41688 36720 1 0 $X=41688 $Y=35552
X4329 VSS VDD ICV_32 $T=45360 17280 0 0 $X=45360 $Y=17192
X4330 VSS VDD ICV_32 $T=45576 19440 1 0 $X=45576 $Y=18272
X4331 VSS VDD ICV_32 $T=64152 38880 0 0 $X=64152 $Y=38792
X4332 VSS VDD ICV_32 $T=64152 45360 0 0 $X=64152 $Y=45272
X4333 VSS VDD ICV_32 $T=64368 25920 1 0 $X=64368 $Y=24752
X4334 VSS VDD ICV_32 $T=64368 49680 0 0 $X=64368 $Y=49592
X4335 VSS VDD ICV_33 $T=4752 23760 1 0 $X=4752 $Y=22592
X4336 VSS VDD ICV_33 $T=4752 47520 0 0 $X=4752 $Y=47432
X4337 VSS VDD ICV_33 $T=14256 49680 0 0 $X=14256 $Y=49592
X4338 VSS VDD ICV_33 $T=14256 54000 0 0 $X=14256 $Y=53912
X4339 VSS VDD ICV_33 $T=33912 34560 1 0 $X=33912 $Y=33392
X4340 VSS VDD ICV_33 $T=37368 66960 0 0 $X=37368 $Y=66872
X4341 VSS VDD ICV_33 $T=42768 6480 0 0 $X=42768 $Y=6392
X4342 VSS VDD ICV_33 $T=44496 25920 1 0 $X=44496 $Y=24752
X4343 VSS VDD ICV_33 $T=52920 45360 0 0 $X=52920 $Y=45272
X4344 VSS VDD ICV_33 $T=61344 15120 1 0 $X=61344 $Y=13952
X4345 VSS VDD ICV_33 $T=64368 12960 1 0 $X=64368 $Y=11792
X4438 VSS VDD 119 85 106 DFFHQNx1_ASAP7_75t_R $T=4752 28080 0 0 $X=4752 $Y=27992
X4439 VSS VDD 119 92 96 DFFHQNx1_ASAP7_75t_R $T=5616 15120 1 0 $X=5616 $Y=13952
X4440 VSS VDD 119 103 125 DFFHQNx1_ASAP7_75t_R $T=6696 23760 1 0 $X=6696 $Y=22592
X4441 VSS VDD 119 122 97 DFFHQNx1_ASAP7_75t_R $T=9288 28080 1 0 $X=9288 $Y=26912
X4442 VSS VDD 177 126 129 DFFHQNx1_ASAP7_75t_R $T=9936 47520 0 0 $X=9936 $Y=47432
X4443 VSS VDD 177 47 4 DFFHQNx1_ASAP7_75t_R $T=13176 43200 0 0 $X=13176 $Y=43112
X4444 VSS VDD 119 609 160 DFFHQNx1_ASAP7_75t_R $T=14472 17280 1 0 $X=14472 $Y=16112
X4445 VSS VDD 119 150 154 DFFHQNx1_ASAP7_75t_R $T=14688 21600 1 0 $X=14688 $Y=20432
X4446 VSS VDD 119 151 165 DFFHQNx1_ASAP7_75t_R $T=14688 23760 0 0 $X=14688 $Y=23672
X4447 VSS VDD 119 155 138 DFFHQNx1_ASAP7_75t_R $T=15120 12960 0 0 $X=15120 $Y=12872
X4448 VSS VDD 177 159 166 DFFHQNx1_ASAP7_75t_R $T=15336 47520 1 0 $X=15336 $Y=46352
X4449 VSS VDD 119 640 167 DFFHQNx1_ASAP7_75t_R $T=15552 36720 0 0 $X=15552 $Y=36632
X4450 VSS VDD 182 483 171 DFFHQNx1_ASAP7_75t_R $T=16200 30240 1 0 $X=16200 $Y=29072
X4451 VSS VDD 177 162 14 DFFHQNx1_ASAP7_75t_R $T=16200 54000 0 0 $X=16200 $Y=53912
X4452 VSS VDD 177 158 172 DFFHQNx1_ASAP7_75t_R $T=16416 41040 1 0 $X=16416 $Y=39872
X4453 VSS VDD 182 488 187 DFFHQNx1_ASAP7_75t_R $T=21600 25920 1 0 $X=21600 $Y=24752
X4454 VSS VDD 177 587 11 DFFHQNx1_ASAP7_75t_R $T=21816 58320 0 0 $X=21816 $Y=58232
X4455 VSS VDD 16 204 10 DFFHQNx1_ASAP7_75t_R $T=25056 54000 0 0 $X=25056 $Y=53912
X4456 VSS VDD 182 209 499 DFFHQNx1_ASAP7_75t_R $T=27648 30240 0 0 $X=27648 $Y=30152
X4457 VSS VDD 182 213 221 DFFHQNx1_ASAP7_75t_R $T=27648 32400 0 0 $X=27648 $Y=32312
X4458 VSS VDD 16 235 223 DFFHQNx1_ASAP7_75t_R $T=30240 43200 1 0 $X=30240 $Y=42032
X4459 VSS VDD 16 228 22 DFFHQNx1_ASAP7_75t_R $T=31320 47520 0 0 $X=31320 $Y=47432
X4460 VSS VDD 182 230 234 DFFHQNx1_ASAP7_75t_R $T=31536 32400 1 0 $X=31536 $Y=31232
X4461 VSS VDD 28 516 293 DFFHQNx1_ASAP7_75t_R $T=37152 45360 0 0 $X=37152 $Y=45272
X4462 VSS VDD 27 515 296 DFFHQNx1_ASAP7_75t_R $T=37800 23760 1 0 $X=37800 $Y=22592
X4463 VSS VDD 28 643 304 DFFHQNx1_ASAP7_75t_R $T=38880 43200 0 0 $X=38880 $Y=43112
X4464 VSS VDD 27 523 305 DFFHQNx1_ASAP7_75t_R $T=39096 28080 0 0 $X=39096 $Y=27992
X4465 VSS VDD 27 291 317 DFFHQNx1_ASAP7_75t_R $T=41040 17280 0 0 $X=41040 $Y=17192
X4466 VSS VDD 27 328 312 DFFHQNx1_ASAP7_75t_R $T=42336 10800 0 0 $X=42336 $Y=10712
X4467 VSS VDD 27 528 300 DFFHQNx1_ASAP7_75t_R $T=42552 21600 1 0 $X=42552 $Y=20432
X4468 VSS VDD 27 318 596 DFFHQNx1_ASAP7_75t_R $T=44496 15120 0 0 $X=44496 $Y=15032
X4469 VSS VDD 27 346 549 DFFHQNx1_ASAP7_75t_R $T=48816 12960 1 0 $X=48816 $Y=11792
X4470 VSS VDD 28 547 385 DFFHQNx1_ASAP7_75t_R $T=49680 49680 1 0 $X=49680 $Y=48512
X4471 VSS VDD 28 616 555 DFFHQNx1_ASAP7_75t_R $T=50976 43200 1 0 $X=50976 $Y=42032
X4472 VSS VDD 374 375 412 DFFHQNx1_ASAP7_75t_R $T=52920 58320 1 0 $X=52920 $Y=57152
X4473 VSS VDD 374 376 401 DFFHQNx1_ASAP7_75t_R $T=52920 58320 0 0 $X=52920 $Y=58232
X4474 VSS VDD 381 386 402 DFFHQNx1_ASAP7_75t_R $T=53136 12960 1 0 $X=53136 $Y=11792
X4475 VSS VDD 381 382 417 DFFHQNx1_ASAP7_75t_R $T=54432 25920 0 0 $X=54432 $Y=25832
X4476 VSS VDD 381 398 421 DFFHQNx1_ASAP7_75t_R $T=55512 25920 1 0 $X=55512 $Y=24752
X4477 VSS VDD 374 665 427 DFFHQNx1_ASAP7_75t_R $T=55944 38880 0 0 $X=55944 $Y=38792
X4478 VSS VDD 381 564 440 DFFHQNx1_ASAP7_75t_R $T=58320 12960 0 0 $X=58320 $Y=12872
X4479 VSS VDD 381 566 554 DFFHQNx1_ASAP7_75t_R $T=58968 32400 0 0 $X=58968 $Y=32312
X4480 VSS VDD 381 426 448 DFFHQNx1_ASAP7_75t_R $T=59184 23760 0 0 $X=59184 $Y=23672
X4481 VSS VDD 381 620 428 DFFHQNx1_ASAP7_75t_R $T=59400 17280 1 0 $X=59400 $Y=16112
X4482 VSS VDD 381 631 474 DFFHQNx1_ASAP7_75t_R $T=62856 28080 0 0 $X=62856 $Y=27992
X4483 VSS VDD 381 573 469 DFFHQNx1_ASAP7_75t_R $T=63288 15120 1 0 $X=63288 $Y=13952
X4484 VSS VDD 381 651 467 DFFHQNx1_ASAP7_75t_R $T=63504 30240 0 0 $X=63504 $Y=30152
X4485 VSS VDD 374 449 455 DFFHQNx1_ASAP7_75t_R $T=63504 58320 0 0 $X=63504 $Y=58232
X4486 VSS VDD 374 453 475 DFFHQNx1_ASAP7_75t_R $T=64152 47520 0 0 $X=64152 $Y=47432
X4623 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 30240 0 0 $X=4752 $Y=30152
X4624 VSS VDD DECAPx10_ASAP7_75t_R $T=8856 17280 1 0 $X=8856 $Y=16112
X4625 VSS VDD DECAPx10_ASAP7_75t_R $T=9720 19440 0 0 $X=9720 $Y=19352
X4626 VSS VDD DECAPx10_ASAP7_75t_R $T=14256 54000 1 0 $X=14256 $Y=52832
X4627 VSS VDD DECAPx10_ASAP7_75t_R $T=14256 56160 1 0 $X=14256 $Y=54992
X4628 VSS VDD DECAPx10_ASAP7_75t_R $T=14256 58320 1 0 $X=14256 $Y=57152
X4629 VSS VDD DECAPx10_ASAP7_75t_R $T=14472 49680 1 0 $X=14472 $Y=48512
X4630 VSS VDD DECAPx10_ASAP7_75t_R $T=16416 28080 1 0 $X=16416 $Y=26912
X4631 VSS VDD DECAPx10_ASAP7_75t_R $T=16632 45360 1 0 $X=16632 $Y=44192
X4632 VSS VDD DECAPx10_ASAP7_75t_R $T=17280 34560 0 0 $X=17280 $Y=34472
X4633 VSS VDD DECAPx10_ASAP7_75t_R $T=17496 30240 0 0 $X=17496 $Y=30152
X4634 VSS VDD DECAPx10_ASAP7_75t_R $T=24408 34560 1 0 $X=24408 $Y=33392
X4635 VSS VDD DECAPx10_ASAP7_75t_R $T=28512 45360 0 0 $X=28512 $Y=45272
X4636 VSS VDD DECAPx10_ASAP7_75t_R $T=31104 49680 1 0 $X=31104 $Y=48512
X4637 VSS VDD DECAPx10_ASAP7_75t_R $T=32616 28080 0 0 $X=32616 $Y=27992
X4638 VSS VDD DECAPx10_ASAP7_75t_R $T=35424 25920 1 0 $X=35424 $Y=24752
X4639 VSS VDD DECAPx10_ASAP7_75t_R $T=37584 51840 0 0 $X=37584 $Y=51752
X4640 VSS VDD DECAPx10_ASAP7_75t_R $T=39960 60480 0 0 $X=39960 $Y=60392
X4641 VSS VDD DECAPx10_ASAP7_75t_R $T=41472 45360 0 0 $X=41472 $Y=45272
X4642 VSS VDD DECAPx10_ASAP7_75t_R $T=43416 15120 1 0 $X=43416 $Y=13952
X4643 VSS VDD DECAPx10_ASAP7_75t_R $T=43848 41040 0 0 $X=43848 $Y=40952
X4644 VSS VDD DECAPx10_ASAP7_75t_R $T=48168 10800 1 0 $X=48168 $Y=9632
X4645 VSS VDD DECAPx10_ASAP7_75t_R $T=51408 21600 1 0 $X=51408 $Y=20432
X4646 VSS VDD DECAPx10_ASAP7_75t_R $T=53568 21600 0 0 $X=53568 $Y=21512
X4647 VSS VDD DECAPx10_ASAP7_75t_R $T=54648 17280 1 0 $X=54648 $Y=16112
X4648 VSS VDD DECAPx10_ASAP7_75t_R $T=55728 10800 0 0 $X=55728 $Y=10712
X4649 VSS VDD DECAPx10_ASAP7_75t_R $T=57240 58320 0 0 $X=57240 $Y=58232
X4650 VSS VDD DECAPx10_ASAP7_75t_R $T=57672 43200 1 0 $X=57672 $Y=42032
X4651 VSS VDD DECAPx10_ASAP7_75t_R $T=58752 25920 0 0 $X=58752 $Y=25832
X4652 VSS VDD DECAPx10_ASAP7_75t_R $T=63504 23760 0 0 $X=63504 $Y=23672
X4653 VSS VDD DECAPx10_ASAP7_75t_R $T=63936 58320 1 0 $X=63936 $Y=57152
X4654 VSS VDD 164 109 168 7 123 OR4x1_ASAP7_75t_R $T=10800 30240 1 0 $X=10800 $Y=29072
X4655 VSS VDD 164 168 7 133 120 OR4x1_ASAP7_75t_R $T=11232 30240 0 0 $X=11232 $Y=30152
X4656 VSS VDD 205 51 491 194 168 OR4x1_ASAP7_75t_R $T=23760 38880 1 0 $X=23760 $Y=37712
X4657 VSS VDD 24 164 245 254 504 OR4x1_ASAP7_75t_R $T=34344 56160 0 0 $X=34344 $Y=56072
X4658 VSS VDD 24 25 164 26 256 OR4x1_ASAP7_75t_R $T=36936 38880 1 0 $X=36936 $Y=37712
X4659 VSS VDD 24 524 164 306 674 OR4x1_ASAP7_75t_R $T=39744 38880 1 0 $X=39744 $Y=37712
X4660 VSS VDD 24 280 164 290 284 OR4x1_ASAP7_75t_R $T=39960 49680 1 0 $X=39960 $Y=48512
X4661 VSS VDD 339 68 301 529 292 OR4x1_ASAP7_75t_R $T=42120 28080 1 0 $X=42120 $Y=26912
X4662 VSS VDD 545 333 338 317 574 OR4x1_ASAP7_75t_R $T=48384 28080 0 0 $X=48384 $Y=27992
X4663 VSS VDD 574 41 7 467 434 OR4x1_ASAP7_75t_R $T=61128 30240 1 0 $X=61128 $Y=29072
X4664 VSS VDD 353 623 406 475 678 OR4x1_ASAP7_75t_R $T=63288 45360 1 0 $X=63288 $Y=44192
X4665 VSS VDD 451 450 623 441 321 OR4x1_ASAP7_75t_R $T=63936 43200 1 0 $X=63936 $Y=42032
X4666 VSS VDD 451 450 623 441 41 OR4x1_ASAP7_75t_R $T=63936 43200 0 0 $X=63936 $Y=43112
X4812 VSS VDD 82 142 109 607 AND3x1_ASAP7_75t_R $T=10152 32400 0 0 $X=10152 $Y=32190
X4813 VSS VDD 133 157 153 480 AND3x1_ASAP7_75t_R $T=12528 34560 1 0 $X=12528 $Y=33392
X4814 VSS VDD 485 273 6 145 AND3x1_ASAP7_75t_R $T=14040 36720 1 0 $X=14040 $Y=35552
X4815 VSS VDD 140 608 153 164 AND3x1_ASAP7_75t_R $T=15120 34560 1 0 $X=15120 $Y=33392
X4816 VSS VDD 627 57 482 639 AND3x1_ASAP7_75t_R $T=15336 41040 0 0 $X=15336 $Y=40830
X4817 VSS VDD 171 167 172 482 AND3x1_ASAP7_75t_R $T=15768 38880 1 0 $X=15768 $Y=37712
X4818 VSS VDD 12 185 191 193 AND3x1_ASAP7_75t_R $T=22032 54000 0 0 $X=22032 $Y=53790
X4819 VSS VDD 652 17 15 180 AND3x1_ASAP7_75t_R $T=25056 43200 1 0 $X=25056 $Y=42032
X4820 VSS VDD 187 210 499 211 AND3x1_ASAP7_75t_R $T=26568 41040 0 0 $X=26568 $Y=40830
X4821 VSS VDD 23 21 489 494 AND3x1_ASAP7_75t_R $T=27648 49680 0 0 $X=27648 $Y=49470
X4822 VSS VDD 223 168 211 496 AND3x1_ASAP7_75t_R $T=29592 43200 0 0 $X=29592 $Y=42990
X4823 VSS VDD 499 223 652 222 AND3x1_ASAP7_75t_R $T=29808 41040 0 0 $X=29808 $Y=40830
X4824 VSS VDD 221 18 489 227 AND3x1_ASAP7_75t_R $T=30888 34560 1 0 $X=30888 $Y=33392
X4825 VSS VDD 221 234 224 591 AND3x1_ASAP7_75t_R $T=31968 32400 0 0 $X=31968 $Y=32190
X4826 VSS VDD 223 293 211 246 AND3x1_ASAP7_75t_R $T=33480 43200 0 0 $X=33480 $Y=42990
X4827 VSS VDD 655 501 265 216 AND3x1_ASAP7_75t_R $T=33912 30240 0 0 $X=33912 $Y=30030
X4828 VSS VDD 273 152 267 663 AND3x1_ASAP7_75t_R $T=36720 49680 0 0 $X=36720 $Y=49470
X4829 VSS VDD 73 273 152 514 AND3x1_ASAP7_75t_R $T=37152 32400 1 0 $X=37152 $Y=31232
X4830 VSS VDD 534 308 331 323 AND3x1_ASAP7_75t_R $T=44496 58320 1 0 $X=44496 $Y=57152
X4831 VSS VDD 282 308 331 537 AND3x1_ASAP7_75t_R $T=44712 60480 0 0 $X=44712 $Y=60270
X4832 VSS VDD 296 300 305 538 AND3x1_ASAP7_75t_R $T=45576 23760 1 0 $X=45576 $Y=22592
X4833 VSS VDD 343 337 351 366 AND3x1_ASAP7_75t_R $T=49464 28080 1 0 $X=49464 $Y=26912
X4834 VSS VDD 73 353 371 600 AND3x1_ASAP7_75t_R $T=50112 34560 1 0 $X=50112 $Y=33392
X4835 VSS VDD 546 75 347 616 AND3x1_ASAP7_75t_R $T=50328 45360 0 0 $X=50328 $Y=45150
X4836 VSS VDD 73 438 372 398 AND3x1_ASAP7_75t_R $T=54216 25920 1 0 $X=54216 $Y=24752
X4837 VSS VDD 385 555 394 633 AND3x1_ASAP7_75t_R $T=54648 38880 0 0 $X=54648 $Y=38670
X4838 VSS VDD 385 435 373 395 AND3x1_ASAP7_75t_R $T=54864 45360 0 0 $X=54864 $Y=45150
X4839 VSS VDD 401 412 373 558 AND3x1_ASAP7_75t_R $T=56160 56160 1 0 $X=56160 $Y=54992
X4840 VSS VDD 393 554 392 407 AND3x1_ASAP7_75t_R $T=56376 43200 1 0 $X=56376 $Y=42032
X4841 VSS VDD 392 408 422 560 AND3x1_ASAP7_75t_R $T=57024 45360 1 0 $X=57024 $Y=44192
X4842 VSS VDD 401 414 561 562 AND3x1_ASAP7_75t_R $T=57672 56160 1 0 $X=57672 $Y=54992
X4843 VSS VDD 401 412 410 435 AND3x1_ASAP7_75t_R $T=58320 56160 0 0 $X=58320 $Y=55950
X4844 VSS VDD 422 424 414 373 AND3x1_ASAP7_75t_R $T=58536 45360 0 0 $X=58536 $Y=45150
X4845 VSS VDD 446 427 399 657 AND3x1_ASAP7_75t_R $T=62856 38880 0 0 $X=62856 $Y=38670
X4846 VSS VDD 73 671 678 453 AND3x1_ASAP7_75t_R $T=62856 47520 0 0 $X=62856 $Y=47310
X4847 VSS VDD 475 424 456 603 AND3x1_ASAP7_75t_R $T=63720 47520 1 0 $X=63720 $Y=46352
X4848 VSS VDD 475 460 400 576 AND3x1_ASAP7_75t_R $T=63720 51840 1 0 $X=63720 $Y=50672
X4849 VSS VDD 96 116 42 NAND2x1_ASAP7_75t_R $T=6696 15120 0 0 $X=6696 $Y=15032
X4850 VSS VDD 118 116 112 NAND2x1_ASAP7_75t_R $T=9504 19440 1 0 $X=9504 $Y=18272
X4851 VSS VDD 132 116 127 NAND2x1_ASAP7_75t_R $T=9936 15120 1 0 $X=9936 $Y=13952
X4852 VSS VDD 125 116 43 NAND2x1_ASAP7_75t_R $T=11016 23760 1 0 $X=11016 $Y=22592
X4853 VSS VDD 157 153 6 NAND2x1_ASAP7_75t_R $T=13176 38880 1 0 $X=13176 $Y=37712
X4854 VSS VDD 138 116 139 NAND2x1_ASAP7_75t_R $T=13824 12960 1 0 $X=13824 $Y=11792
X4855 VSS VDD 4 172 638 NAND2x1_ASAP7_75t_R $T=14256 36720 0 0 $X=14256 $Y=36632
X4856 VSS VDD 165 116 46 NAND2x1_ASAP7_75t_R $T=14904 25920 1 0 $X=14904 $Y=24752
X4857 VSS VDD 273 152 3 NAND2x1_ASAP7_75t_R $T=16200 49680 0 0 $X=16200 $Y=49592
X4858 VSS VDD 160 116 48 NAND2x1_ASAP7_75t_R $T=16632 15120 0 0 $X=16632 $Y=15032
X4859 VSS VDD 154 116 156 NAND2x1_ASAP7_75t_R $T=16848 19440 0 0 $X=16848 $Y=19352
X4860 VSS VDD 14 173 49 NAND2x1_ASAP7_75t_R $T=19656 51840 1 0 $X=19656 $Y=50672
X4861 VSS VDD 14 12 175 NAND2x1_ASAP7_75t_R $T=21600 56160 0 0 $X=21600 $Y=56072
X4862 VSS VDD 273 480 185 NAND2x1_ASAP7_75t_R $T=22896 49680 1 0 $X=22896 $Y=48512
X4863 VSS VDD 17 15 491 NAND2x1_ASAP7_75t_R $T=23328 41040 1 0 $X=23328 $Y=39872
X4864 VSS VDD 202 197 50 NAND2x1_ASAP7_75t_R $T=23544 36720 1 0 $X=23544 $Y=35552
X4865 VSS VDD 202 195 51 NAND2x1_ASAP7_75t_R $T=23544 36720 0 0 $X=23544 $Y=36632
X4866 VSS VDD 18 180 588 NAND2x1_ASAP7_75t_R $T=26136 38880 1 0 $X=26136 $Y=37712
X4867 VSS VDD 507 239 60 NAND2x1_ASAP7_75t_R $T=36072 34560 0 0 $X=36072 $Y=34472
X4868 VSS VDD 300 339 63 NAND2x1_ASAP7_75t_R $T=41256 21600 1 0 $X=41256 $Y=20432
X4869 VSS VDD 319 339 302 NAND2x1_ASAP7_75t_R $T=41472 23760 0 0 $X=41472 $Y=23672
X4870 VSS VDD 529 310 65 NAND2x1_ASAP7_75t_R $T=43416 28080 0 0 $X=43416 $Y=27992
X4871 VSS VDD 331 286 322 NAND2x1_ASAP7_75t_R $T=44496 54000 0 0 $X=44496 $Y=53912
X4872 VSS VDD 312 80 66 NAND2x1_ASAP7_75t_R $T=45144 8640 0 0 $X=45144 $Y=8552
X4873 VSS VDD 301 333 344 NAND2x1_ASAP7_75t_R $T=45792 28080 0 0 $X=45792 $Y=27992
X4874 VSS VDD 320 80 67 NAND2x1_ASAP7_75t_R $T=46440 8640 0 0 $X=46440 $Y=8552
X4875 VSS VDD 308 331 68 NAND2x1_ASAP7_75t_R $T=46440 32400 0 0 $X=46440 $Y=32312
X4876 VSS VDD 351 359 371 NAND2x1_ASAP7_75t_R $T=48816 30240 0 0 $X=48816 $Y=30152
X4877 VSS VDD 324 305 545 NAND2x1_ASAP7_75t_R $T=49248 23760 0 0 $X=49248 $Y=23672
X4878 VSS VDD 321 378 75 NAND2x1_ASAP7_75t_R $T=52056 45360 1 0 $X=52056 $Y=44192
X4879 VSS VDD 383 80 76 NAND2x1_ASAP7_75t_R $T=53784 8640 0 0 $X=53784 $Y=8552
X4880 VSS VDD 397 80 403 NAND2x1_ASAP7_75t_R $T=56160 17280 0 0 $X=56160 $Y=17192
X4881 VSS VDD 399 394 77 NAND2x1_ASAP7_75t_R $T=58968 36720 0 0 $X=58968 $Y=36632
X4882 VSS VDD 414 435 451 NAND2x1_ASAP7_75t_R $T=62640 43200 0 0 $X=62640 $Y=43112
X4883 VSS VDD 444 80 572 NAND2x1_ASAP7_75t_R $T=63072 12960 1 0 $X=63072 $Y=11792
X4884 VSS VDD 447 80 78 NAND2x1_ASAP7_75t_R $T=63288 19440 0 0 $X=63288 $Y=19352
X4885 VSS VDD 577 80 621 NAND2x1_ASAP7_75t_R $T=63504 23760 1 0 $X=63504 $Y=22592
X4886 VSS VDD 469 80 79 NAND2x1_ASAP7_75t_R $T=63720 17280 1 0 $X=63720 $Y=16112
X4887 VSS VDD 394 672 80 NAND2x1_ASAP7_75t_R $T=63720 32400 0 0 $X=63720 $Y=32312
X4901 VSS VDD 153 157 109 AND2x2_ASAP7_75t_R $T=13824 34560 1 0 $X=13824 $Y=33392
X4902 VSS VDD 172 4 153 AND2x2_ASAP7_75t_R $T=14472 38880 1 0 $X=14472 $Y=37712
X4903 VSS VDD 152 273 163 AND2x2_ASAP7_75t_R $T=17280 51840 0 0 $X=17280 $Y=51752
X4904 VSS VDD 57 480 137 AND2x2_ASAP7_75t_R $T=18360 47520 0 0 $X=18360 $Y=47432
X4905 VSS VDD 186 73 488 AND2x2_ASAP7_75t_R $T=22032 25920 0 0 $X=22032 $Y=25832
X4906 VSS VDD 17 652 199 AND2x2_ASAP7_75t_R $T=25056 43200 0 0 $X=25056 $Y=43112
X4907 VSS VDD 200 73 196 AND2x2_ASAP7_75t_R $T=25488 28080 1 0 $X=25488 $Y=26912
X4908 VSS VDD 57 152 219 AND2x2_ASAP7_75t_R $T=28512 41040 0 0 $X=28512 $Y=40952
X4909 VSS VDD 180 18 224 AND2x2_ASAP7_75t_R $T=30456 38880 1 0 $X=30456 $Y=37712
X4910 VSS VDD 257 168 271 AND2x2_ASAP7_75t_R $T=35856 45360 0 0 $X=35856 $Y=45272
X4911 VSS VDD 70 73 327 AND2x2_ASAP7_75t_R $T=49680 19440 0 0 $X=49680 $Y=19352
X4912 VSS VDD 359 351 369 AND2x2_ASAP7_75t_R $T=50328 30240 1 0 $X=50328 $Y=29072
X4913 VSS VDD 415 321 362 AND2x2_ASAP7_75t_R $T=50544 47520 1 0 $X=50544 $Y=46352
X4914 VSS VDD 330 345 548 AND2x2_ASAP7_75t_R $T=50976 38880 0 0 $X=50976 $Y=38792
X4915 VSS VDD 371 353 36 AND2x2_ASAP7_75t_R $T=51624 45360 0 0 $X=51624 $Y=45272
X4916 VSS VDD 617 73 376 AND2x2_ASAP7_75t_R $T=51624 58320 0 0 $X=51624 $Y=58232
X4917 VSS VDD 363 546 379 AND2x2_ASAP7_75t_R $T=51840 47520 1 0 $X=51840 $Y=46352
X4918 VSS VDD 373 401 388 AND2x2_ASAP7_75t_R $T=53136 56160 1 0 $X=53136 $Y=54992
X4919 VSS VDD 418 73 566 AND2x2_ASAP7_75t_R $T=59184 32400 1 0 $X=59184 $Y=31232
X4920 VSS VDD 424 422 400 AND2x2_ASAP7_75t_R $T=59832 45360 1 0 $X=59832 $Y=44192
X4921 VSS VDD 433 546 442 AND2x2_ASAP7_75t_R $T=61344 54000 1 0 $X=61344 $Y=52832
X4922 VSS VDD 456 424 561 AND2x2_ASAP7_75t_R $T=63504 49680 1 0 $X=63504 $Y=48512
X4923 VSS VDD 106 109 606 NOR2x1_ASAP7_75t_R $T=7992 28080 1 0 $X=7992 $Y=26912
X4924 VSS VDD 118 109 117 NOR2x1_ASAP7_75t_R $T=8424 17280 0 0 $X=8424 $Y=17192
X4925 VSS VDD 96 109 111 NOR2x1_ASAP7_75t_R $T=9288 15120 0 0 $X=9288 $Y=15032
X4926 VSS VDD 125 109 104 NOR2x1_ASAP7_75t_R $T=9720 21600 0 0 $X=9720 $Y=21512
X4927 VSS VDD 132 109 637 NOR2x1_ASAP7_75t_R $T=11232 15120 1 0 $X=11232 $Y=13952
X4928 VSS VDD 138 109 481 NOR2x1_ASAP7_75t_R $T=13608 15120 1 0 $X=13608 $Y=13952
X4929 VSS VDD 154 109 161 NOR2x1_ASAP7_75t_R $T=15552 21600 0 0 $X=15552 $Y=21512
X4930 VSS VDD 160 109 146 NOR2x1_ASAP7_75t_R $T=15768 19440 1 0 $X=15768 $Y=18272
X4931 VSS VDD 167 163 148 NOR2x1_ASAP7_75t_R $T=16848 36720 1 0 $X=16848 $Y=35552
X4932 VSS VDD 10 208 641 NOR2x1_ASAP7_75t_R $T=26136 56160 1 0 $X=26136 $Y=54992
X4933 VSS VDD 57 494 212 NOR2x1_ASAP7_75t_R $T=27216 45360 0 0 $X=27216 $Y=45272
X4934 VSS VDD 214 231 213 NOR2x1_ASAP7_75t_R $T=29376 34560 0 0 $X=29376 $Y=34472
X4935 VSS VDD 218 497 228 NOR2x1_ASAP7_75t_R $T=30024 47520 0 0 $X=30024 $Y=47432
X4936 VSS VDD 164 242 505 NOR2x1_ASAP7_75t_R $T=33696 62640 0 0 $X=33696 $Y=62552
X4937 VSS VDD 164 258 642 NOR2x1_ASAP7_75t_R $T=36288 51840 0 0 $X=36288 $Y=51752
X4938 VSS VDD 612 164 275 NOR2x1_ASAP7_75t_R $T=37368 64800 1 0 $X=37368 $Y=63632
X4939 VSS VDD 317 343 350 NOR2x1_ASAP7_75t_R $T=47736 30240 1 0 $X=47736 $Y=29072
X4940 VSS VDD 343 545 340 NOR2x1_ASAP7_75t_R $T=50112 25920 0 0 $X=50112 $Y=25832
X4941 VSS VDD 7 74 551 NOR2x1_ASAP7_75t_R $T=51840 51840 1 0 $X=51840 $Y=50672
X4942 VSS VDD 401 373 380 NOR2x1_ASAP7_75t_R $T=51840 56160 1 0 $X=51840 $Y=54992
X4943 VSS VDD 7 552 664 NOR2x1_ASAP7_75t_R $T=54648 28080 0 0 $X=54648 $Y=27992
X4944 VSS VDD 7 419 567 NOR2x1_ASAP7_75t_R $T=59184 47520 0 0 $X=59184 $Y=47432
X4945 VSS VDD 450 574 672 NOR2x1_ASAP7_75t_R $T=63504 34560 1 0 $X=63504 $Y=33392
X4946 VSS VDD 188 73 159 ICV_43 $T=21384 45360 1 0 $X=21384 $Y=44192
X4947 VSS VDD 166 203 15 ICV_43 $T=22896 45360 1 0 $X=22896 $Y=44192
X4948 VSS VDD 195 202 13 ICV_43 $T=24840 36720 0 0 $X=24840 $Y=36632
X4949 VSS VDD 15 17 668 ICV_43 $T=25056 41040 0 0 $X=25056 $Y=40952
X4950 VSS VDD 276 73 643 ICV_43 $T=38880 45360 1 0 $X=38880 $Y=44192
X4951 VSS VDD 331 308 345 ICV_43 $T=44928 32400 0 0 $X=44928 $Y=32312
X4952 VSS VDD 435 414 452 ICV_43 $T=61128 43200 0 0 $X=61128 $Y=43112
X4953 VSS VDD 649 73 449 ICV_43 $T=61992 58320 0 0 $X=61992 $Y=58232
X4954 VSS VDD 554 393 456 ICV_43 $T=62424 43200 1 0 $X=62424 $Y=42032
X4955 VSS VDD 650 73 602 ICV_43 $T=62424 58320 1 0 $X=62424 $Y=57152
X4956 VSS VDD 81 73 651 ICV_43 $T=63504 32400 1 0 $X=63504 $Y=31232
X4974 VSS VDD 177 170 202 ICV_45 $T=18144 36720 1 0 $X=18144 $Y=35552
X4975 VSS VDD 27 314 320 ICV_45 $T=42768 10800 1 0 $X=42768 $Y=9632
X4976 VSS VDD 27 646 370 ICV_45 $T=48600 17280 1 0 $X=48600 $Y=16112
X4977 VSS VDD 374 413 436 ICV_45 $T=56160 49680 0 0 $X=56160 $Y=49592
X4978 VSS VDD 381 648 577 ICV_45 $T=60264 21600 0 0 $X=60264 $Y=21512
X4979 VSS VDD 381 571 444 ICV_45 $T=60480 10800 0 0 $X=60480 $Y=10712
X4980 VSS VDD 381 445 447 ICV_45 $T=61344 21600 1 0 $X=61344 $Y=20432
X4981 VSS VDD 374 670 446 ICV_45 $T=63288 36720 1 0 $X=63288 $Y=35552
X4982 VSS VDD 3 283 270 273 276 OAI22x1_ASAP7_75t_R $T=36720 45360 1 0 $X=36720 $Y=44192
X4983 VSS VDD 536 322 266 331 335 OAI22x1_ASAP7_75t_R $T=45360 54000 1 0 $X=45360 $Y=52832
X4984 VSS VDD 339 614 645 324 70 OAI22x1_ASAP7_75t_R $T=48600 21600 1 0 $X=48600 $Y=20432
X4985 VSS VDD 574 321 461 467 81 OAI22x1_ASAP7_75t_R $T=63288 30240 1 0 $X=63288 $Y=29072
X5090 VSS VDD 119 478 121 ICV_51 $T=6912 32400 1 0 $X=6912 $Y=31232
X5091 VSS VDD 16 184 203 ICV_51 $T=21168 47520 0 0 $X=21168 $Y=47432
X5092 VSS VDD 16 198 12 ICV_51 $T=22464 58320 1 0 $X=22464 $Y=57152
X5093 VSS VDD 182 196 210 ICV_51 $T=23328 25920 0 0 $X=23328 $Y=25832
X5094 VSS VDD 182 216 226 ICV_51 $T=28080 28080 0 0 $X=28080 $Y=27992
X5095 VSS VDD 28 599 377 ICV_51 $T=48600 38880 1 0 $X=48600 $Y=37712
X5096 VSS VDD 28 357 392 ICV_51 $T=50112 43200 0 0 $X=50112 $Y=43112
X5097 VSS VDD 374 364 618 ICV_51 $T=50760 36720 1 0 $X=50760 $Y=35552
X5098 VSS VDD 374 551 408 ICV_51 $T=51624 49680 0 0 $X=51624 $Y=49592
X5099 VSS VDD 28 379 409 ICV_51 $T=53136 47520 1 0 $X=53136 $Y=46352
X5100 VSS VDD 381 553 397 ICV_51 $T=54648 19440 1 0 $X=54648 $Y=18272
X5101 VSS VDD 374 647 410 ICV_51 $T=54648 60480 1 0 $X=54648 $Y=59312
X5102 VSS VDD 27 404 430 ICV_51 $T=56160 21600 1 0 $X=56160 $Y=20432
X5103 VSS VDD 374 602 459 ICV_51 $T=62856 56160 0 0 $X=62856 $Y=56072
X5104 VSS VDD 171 167 140 485 HAxp5_ASAP7_75t_R $T=15336 34560 0 0 $X=15336 $Y=34472
X5105 VSS VDD 585 167 141 680 HAxp5_ASAP7_75t_R $T=16200 32400 0 0 $X=16200 $Y=32312
X5106 VSS VDD 187 210 628 681 HAxp5_ASAP7_75t_R $T=22464 34560 1 0 $X=22464 $Y=33392
X5107 VSS VDD 187 210 201 490 HAxp5_ASAP7_75t_R $T=22680 30240 1 0 $X=22680 $Y=29072
X5108 VSS VDD 282 281 611 534 HAxp5_ASAP7_75t_R $T=38448 62640 0 0 $X=38448 $Y=62552
X5109 VSS VDD 296 300 301 307 HAxp5_ASAP7_75t_R $T=41904 25920 1 0 $X=41904 $Y=24752
X5110 VSS VDD 319 300 333 682 HAxp5_ASAP7_75t_R $T=45360 25920 0 0 $X=45360 $Y=25832
X5111 VSS VDD 296 300 343 683 HAxp5_ASAP7_75t_R $T=46440 25920 1 0 $X=46440 $Y=24752
X5112 VSS VDD 393 554 406 559 HAxp5_ASAP7_75t_R $T=56160 34560 0 0 $X=56160 $Y=34472
X5113 VSS VDD 393 554 441 684 HAxp5_ASAP7_75t_R $T=62856 41040 0 0 $X=62856 $Y=40952
X5114 VSS VDD 163 97 654 73 479 OA211x2_ASAP7_75t_R $T=14688 28080 1 0 $X=14688 $Y=26912
X5115 VSS VDD 163 585 484 73 483 OA211x2_ASAP7_75t_R $T=15768 30240 0 0 $X=15768 $Y=30152
X5116 VSS VDD 179 14 169 176 162 OA211x2_ASAP7_75t_R $T=19224 56160 1 0 $X=19224 $Y=54992
X5117 VSS VDD 183 174 181 73 587 OA211x2_ASAP7_75t_R $T=20736 58320 1 0 $X=20736 $Y=57152
X5118 VSS VDD 190 193 512 73 198 OA211x2_ASAP7_75t_R $T=23328 54000 0 0 $X=23328 $Y=53912
X5119 VSS VDD 208 273 185 10 207 OA211x2_ASAP7_75t_R $T=25056 54000 1 0 $X=25056 $Y=52832
X5120 VSS VDD 493 57 676 73 209 OA211x2_ASAP7_75t_R $T=25272 30240 0 0 $X=25272 $Y=30152
X5121 VSS VDD 588 273 173 221 214 OA211x2_ASAP7_75t_R $T=27000 34560 0 0 $X=27000 $Y=34472
X5122 VSS VDD 658 653 215 73 495 OA211x2_ASAP7_75t_R $T=28728 56160 1 0 $X=28728 $Y=54992
X5123 VSS VDD 532 339 63 73 528 OA211x2_ASAP7_75t_R $T=42120 21600 0 0 $X=42120 $Y=21512
X5124 VSS VDD 80 349 66 73 328 OA211x2_ASAP7_75t_R $T=44928 12960 1 0 $X=44928 $Y=11792
X5125 VSS VDD 80 615 67 73 314 OA211x2_ASAP7_75t_R $T=46656 10800 0 0 $X=46656 $Y=10712
X5126 VSS VDD 341 391 644 546 599 OA211x2_ASAP7_75t_R $T=47952 38880 0 0 $X=47952 $Y=38792
X5127 VSS VDD 342 391 348 73 357 OA211x2_ASAP7_75t_R $T=47952 43200 1 0 $X=47952 $Y=42032
X5128 VSS VDD 278 331 598 353 360 OA211x2_ASAP7_75t_R $T=48168 54000 0 0 $X=48168 $Y=53912
X5129 VSS VDD 354 353 352 546 547 OA211x2_ASAP7_75t_R $T=49248 47520 0 0 $X=49248 $Y=47432
X5130 VSS VDD 666 353 542 546 375 OA211x2_ASAP7_75t_R $T=51192 58320 1 0 $X=51192 $Y=57152
X5131 VSS VDD 369 68 353 393 552 OA211x2_ASAP7_75t_R $T=53136 30240 1 0 $X=53136 $Y=29072
X5132 VSS VDD 80 619 76 73 387 OA211x2_ASAP7_75t_R $T=54000 10800 0 0 $X=54000 $Y=10712
X5133 VSS VDD 80 358 403 73 553 OA211x2_ASAP7_75t_R $T=54000 17280 0 0 $X=54000 $Y=17192
X5134 VSS VDD 389 353 539 546 647 OA211x2_ASAP7_75t_R $T=54216 56160 0 0 $X=54216 $Y=56072
X5135 VSS VDD 77 427 439 546 622 OA211x2_ASAP7_75t_R $T=60264 36720 0 0 $X=60264 $Y=36632
X5136 VSS VDD 80 420 621 73 648 OA211x2_ASAP7_75t_R $T=60696 23760 1 0 $X=60696 $Y=22592
X5137 VSS VDD 80 431 572 73 571 OA211x2_ASAP7_75t_R $T=61128 12960 1 0 $X=61128 $Y=11792
X5138 VSS VDD 80 569 78 73 445 OA211x2_ASAP7_75t_R $T=61560 19440 0 0 $X=61560 $Y=19352
X5139 VSS VDD 80 425 79 73 573 OA211x2_ASAP7_75t_R $T=62856 15120 0 0 $X=62856 $Y=15032
X5158 VSS VDD 119 89 118 ICV_54 $T=4752 19440 1 0 $X=4752 $Y=18272
X5159 VSS VDD 119 115 132 ICV_54 $T=7776 12960 1 0 $X=7776 $Y=11792
X5160 VSS VDD 16 495 23 ICV_54 $T=28728 56160 0 0 $X=28728 $Y=56072
X5161 VSS VDD 28 272 297 ICV_54 $T=37368 34560 0 0 $X=37368 $Y=34472
X5162 VSS VDD 27 327 324 ICV_54 $T=44928 19440 0 0 $X=44928 $Y=19352
X5163 VSS VDD 381 387 383 ICV_54 $T=52920 10800 1 0 $X=52920 $Y=9632
X5164 VSS VDD 381 411 393 ICV_54 $T=56376 30240 1 0 $X=56376 $Y=29072
X5165 VSS VDD 374 442 460 ICV_54 $T=62640 54000 1 0 $X=62640 $Y=52832
X5166 VSS VDD ICV_55 $T=8640 21600 1 0 $X=8640 $Y=20432
X5167 VSS VDD ICV_55 $T=14256 56160 0 0 $X=14256 $Y=56072
X5168 VSS VDD ICV_55 $T=16416 34560 1 0 $X=16416 $Y=33392
X5169 VSS VDD ICV_55 $T=17064 38880 1 0 $X=17064 $Y=37712
X5170 VSS VDD ICV_55 $T=17496 43200 1 0 $X=17496 $Y=42032
X5171 VSS VDD ICV_55 $T=23760 62640 0 0 $X=23760 $Y=62552
X5172 VSS VDD ICV_55 $T=35856 21600 0 0 $X=35856 $Y=21512
X5173 VSS VDD ICV_55 $T=36288 58320 0 0 $X=36288 $Y=58232
X5174 VSS VDD ICV_55 $T=47736 8640 0 0 $X=47736 $Y=8552
X5175 VSS VDD ICV_55 $T=62424 34560 0 0 $X=62424 $Y=34472
X5176 VSS VDD ICV_55 $T=62640 12960 0 0 $X=62640 $Y=12872
X5177 VSS VDD 21 22 23 194 NAND3x1_ASAP7_75t_R $T=27216 47520 1 0 $X=27216 $Y=46352
X5178 VSS VDD 77 321 427 439 NAND3x1_ASAP7_75t_R $T=60048 38880 1 0 $X=60048 $Y=37712
X5179 VSS VDD 399 427 446 450 NAND3x1_ASAP7_75t_R $T=62424 38880 1 0 $X=62424 $Y=37712
X5180 VSS VDD ICV_56 $T=6696 19440 0 0 $X=6696 $Y=19352
X5181 VSS VDD ICV_56 $T=12528 36720 1 0 $X=12528 $Y=35552
X5182 VSS VDD ICV_56 $T=22896 56160 0 0 $X=22896 $Y=56072
X5183 VSS VDD ICV_56 $T=33264 60480 1 0 $X=33264 $Y=59312
X5184 VSS VDD ICV_56 $T=33912 34560 0 0 $X=33912 $Y=34472
X5185 VSS VDD ICV_56 $T=37800 36720 1 0 $X=37800 $Y=35552
X5186 VSS VDD ICV_56 $T=37800 54000 1 0 $X=37800 $Y=52832
X5187 VSS VDD ICV_56 $T=38232 17280 0 0 $X=38232 $Y=17192
X5188 VSS VDD ICV_56 $T=48816 45360 0 0 $X=48816 $Y=45272
X5189 VSS VDD ICV_56 $T=54000 49680 1 0 $X=54000 $Y=48512
X5190 VSS VDD ICV_56 $T=56160 51840 1 0 $X=56160 $Y=50672
X5191 VSS VDD ICV_56 $T=61128 45360 1 0 $X=61128 $Y=44192
X5192 VSS VDD ICV_56 $T=61776 25920 1 0 $X=61776 $Y=24752
X5193 VSS VDD ICV_56 $T=61776 51840 0 0 $X=61776 $Y=51752
X5194 VSS VDD ICV_56 $T=62208 47520 1 0 $X=62208 $Y=46352
X5195 VSS VDD ICV_57 $T=14256 58320 0 0 $X=14256 $Y=58232
X5196 VSS VDD ICV_57 $T=16848 38880 0 0 $X=16848 $Y=38792
X5197 VSS VDD ICV_57 $T=17928 32400 1 0 $X=17928 $Y=31232
X5198 VSS VDD ICV_57 $T=18144 32400 0 0 $X=18144 $Y=32312
X5199 VSS VDD ICV_57 $T=27000 58320 1 0 $X=27000 $Y=57152
X5200 VSS VDD ICV_57 $T=29808 49680 0 0 $X=29808 $Y=49592
X5201 VSS VDD ICV_57 $T=36936 15120 0 0 $X=36936 $Y=15032
X5202 VSS VDD ICV_57 $T=38016 32400 0 0 $X=38016 $Y=32312
X5203 VSS VDD ICV_57 $T=47952 32400 1 0 $X=47952 $Y=31232
X5204 VSS VDD ICV_57 $T=52920 15120 0 0 $X=52920 $Y=15032
X5205 VSS VDD ICV_57 $T=55944 28080 0 0 $X=55944 $Y=27992
X5206 VSS VDD ICV_57 $T=61776 4320 0 0 $X=61776 $Y=4232
X5240 VSS VDD 219 243 7 629 199 661 AO221x1_ASAP7_75t_R $T=25056 47520 1 0 $X=25056 $Y=46352
X5241 VSS VDD 217 219 7 589 494 497 AO221x1_ASAP7_75t_R $T=28944 49680 1 0 $X=28944 $Y=48512
X5242 VSS VDD 219 677 7 498 224 231 AO221x1_ASAP7_75t_R $T=30456 36720 1 0 $X=30456 $Y=35552
X5243 VSS VDD 332 345 321 340 334 539 AO221x1_ASAP7_75t_R $T=44928 56160 0 0 $X=44928 $Y=55930
X5244 VSS VDD 311 345 321 340 332 542 AO221x1_ASAP7_75t_R $T=45792 58320 1 0 $X=45792 $Y=57152
X5245 VSS VDD 329 345 321 340 330 644 AO221x1_ASAP7_75t_R $T=46440 38880 1 0 $X=46440 $Y=37712
X5246 VSS VDD 533 345 321 340 316 347 AO221x1_ASAP7_75t_R $T=46656 43200 0 0 $X=46656 $Y=42970
X5247 VSS VDD 334 345 41 340 533 352 AO221x1_ASAP7_75t_R $T=46872 49680 1 0 $X=46872 $Y=48512
X5248 VSS VDD 388 41 380 36 540 617 AO221x1_ASAP7_75t_R $T=51624 56160 0 0 $X=51624 $Y=55930
X5249 VSS VDD 36 541 575 41 400 671 AO221x1_ASAP7_75t_R $T=61560 49680 0 0 $X=61560 $Y=49450
X5259 VSS VDD 168 673 163 508 188 AO22x1_ASAP7_75t_R $T=20952 49680 1 0 $X=20952 $Y=48512
X5260 VSS VDD 321 561 335 36 656 AO22x1_ASAP7_75t_R $T=57672 51840 1 0 $X=57672 $Y=50672
X5261 VSS VDD 321 432 36 544 649 AO22x1_ASAP7_75t_R $T=60480 58320 1 0 $X=60480 $Y=57152
X5262 VSS VDD 36 355 321 462 650 AO22x1_ASAP7_75t_R $T=60912 56160 0 0 $X=60912 $Y=55982
X5263 VSS VDD ICV_61 $T=4752 36720 1 0 $X=4752 $Y=35552
X5264 VSS VDD ICV_61 $T=4752 38880 1 0 $X=4752 $Y=37712
X5265 VSS VDD ICV_61 $T=4752 38880 0 0 $X=4752 $Y=38792
X5266 VSS VDD ICV_61 $T=4752 43200 0 0 $X=4752 $Y=43112
X5267 VSS VDD ICV_61 $T=4752 49680 1 0 $X=4752 $Y=48512
X5268 VSS VDD ICV_61 $T=17280 41040 0 0 $X=17280 $Y=40952
X5269 VSS VDD ICV_61 $T=23760 62640 1 0 $X=23760 $Y=61472
X5270 VSS VDD ICV_61 $T=23760 64800 0 0 $X=23760 $Y=64712
X5271 VSS VDD ICV_61 $T=26136 58320 0 0 $X=26136 $Y=58232
X5272 VSS VDD ICV_61 $T=26352 36720 0 0 $X=26352 $Y=36632
X5273 VSS VDD ICV_61 $T=33264 10800 0 0 $X=33264 $Y=10712
X5274 VSS VDD ICV_61 $T=37152 19440 0 0 $X=37152 $Y=19352
X5275 VSS VDD ICV_61 $T=38448 12960 0 0 $X=38448 $Y=12872
X5276 VSS VDD ICV_61 $T=38880 47520 1 0 $X=38880 $Y=46352
X5277 VSS VDD ICV_61 $T=40824 47520 0 0 $X=40824 $Y=47432
X5278 VSS VDD ICV_61 $T=41904 34560 1 0 $X=41904 $Y=33392
X5279 VSS VDD ICV_61 $T=42120 34560 0 0 $X=42120 $Y=34472
X5280 VSS VDD ICV_61 $T=50544 23760 0 0 $X=50544 $Y=23672
X5281 VSS VDD ICV_61 $T=50976 19440 0 0 $X=50976 $Y=19352
X5282 VSS VDD ICV_61 $T=54000 51840 0 0 $X=54000 $Y=51752
X5283 VSS VDD ICV_61 $T=54648 41040 0 0 $X=54648 $Y=40952
X5284 VSS VDD ICV_61 $T=59400 62640 0 0 $X=59400 $Y=62552
X5285 VSS VDD ICV_61 $T=59400 64800 1 0 $X=59400 $Y=63632
X5286 VSS VDD ICV_61 $T=59400 66960 0 0 $X=59400 $Y=66872
X5287 VSS VDD ICV_61 $T=59832 62640 1 0 $X=59832 $Y=61472
X5288 VSS VDD ICV_61 $T=59832 66960 1 0 $X=59832 $Y=65792
X5289 VSS VDD ICV_61 $T=60264 64800 0 0 $X=60264 $Y=64712
X5290 VSS VDD ICV_61 $T=60912 28080 1 0 $X=60912 $Y=26912
X5291 VSS VDD ICV_62 $T=4752 21600 0 0 $X=4752 $Y=21512
X5292 VSS VDD ICV_62 $T=12096 19440 1 0 $X=12096 $Y=18272
X5293 VSS VDD ICV_62 $T=12312 30240 1 0 $X=12312 $Y=29072
X5294 VSS VDD ICV_62 $T=14256 25920 0 0 $X=14256 $Y=25832
X5295 VSS VDD ICV_62 $T=15768 28080 0 0 $X=15768 $Y=27992
X5296 VSS VDD ICV_62 $T=36504 69120 1 0 $X=36504 $Y=67952
X5297 VSS VDD ICV_62 $T=37152 34560 1 0 $X=37152 $Y=33392
X5298 VSS VDD ICV_62 $T=40392 45360 1 0 $X=40392 $Y=44192
X5299 VSS VDD ICV_62 $T=45792 54000 0 0 $X=45792 $Y=53912
X5300 VSS VDD ICV_62 $T=49464 51840 1 0 $X=49464 $Y=50672
X5301 VSS VDD ICV_62 $T=52272 38880 0 0 $X=52272 $Y=38792
X5302 VSS VDD ICV_62 $T=56592 36720 0 0 $X=56592 $Y=36632
X5303 VSS VDD ICV_62 $T=60480 47520 0 0 $X=60480 $Y=47432
X5304 VSS VDD 94 109 104 108 AO21x1_ASAP7_75t_R $T=7344 21600 1 0 $X=7344 $Y=20432
X5305 VSS VDD 90 109 117 110 AO21x1_ASAP7_75t_R $T=7560 17280 1 0 $X=7560 $Y=16112
X5306 VSS VDD 635 109 111 113 AO21x1_ASAP7_75t_R $T=7992 15120 0 0 $X=7992 $Y=14958
X5307 VSS VDD 101 109 606 114 AO21x1_ASAP7_75t_R $T=7992 23760 0 0 $X=7992 $Y=23598
X5308 VSS VDD 121 6 607 130 AO21x1_ASAP7_75t_R $T=10584 34560 1 0 $X=10584 $Y=33392
X5309 VSS VDD 100 109 637 134 AO21x1_ASAP7_75t_R $T=11448 12960 0 0 $X=11448 $Y=12798
X5310 VSS VDD 93 109 481 143 AO21x1_ASAP7_75t_R $T=13824 15120 0 0 $X=13824 $Y=14958
X5311 VSS VDD 105 109 161 147 AO21x1_ASAP7_75t_R $T=14256 21600 0 0 $X=14256 $Y=21438
X5312 VSS VDD 131 109 146 149 AO21x1_ASAP7_75t_R $T=14472 19440 1 0 $X=14472 $Y=18272
X5313 VSS VDD 4 486 639 5 AO21x1_ASAP7_75t_R $T=15336 45360 1 0 $X=15336 $Y=44192
X5314 VSS VDD 168 180 49 176 AO21x1_ASAP7_75t_R $T=19872 51840 0 0 $X=19872 $Y=51678
X5315 VSS VDD 163 263 586 183 AO21x1_ASAP7_75t_R $T=21168 56160 1 0 $X=21168 $Y=54992
X5316 VSS VDD 187 168 487 186 AO21x1_ASAP7_75t_R $T=21600 28080 1 0 $X=21600 $Y=26912
X5317 VSS VDD 14 489 273 191 AO21x1_ASAP7_75t_R $T=22464 51840 0 0 $X=22464 $Y=51678
X5318 VSS VDD 490 168 492 200 AO21x1_ASAP7_75t_R $T=24192 28080 1 0 $X=24192 $Y=26912
X5319 VSS VDD 163 590 496 220 AO21x1_ASAP7_75t_R $T=29808 45360 1 0 $X=29808 $Y=44192
X5320 VSS VDD 152 264 168 250 AO21x1_ASAP7_75t_R $T=34128 36720 0 0 $X=34128 $Y=36558
X5321 VSS VDD 152 675 168 265 AO21x1_ASAP7_75t_R $T=35856 32400 1 0 $X=35856 $Y=31232
X5322 VSS VDD 251 164 275 267 AO21x1_ASAP7_75t_R $T=36072 64800 1 0 $X=36072 $Y=63632
X5323 VSS VDD 152 287 168 269 AO21x1_ASAP7_75t_R $T=36288 36720 0 0 $X=36288 $Y=36558
X5324 VSS VDD 301 345 321 310 AO21x1_ASAP7_75t_R $T=44928 30240 1 0 $X=44928 $Y=29072
X5325 VSS VDD 534 340 537 38 AO21x1_ASAP7_75t_R $T=44928 58320 0 0 $X=44928 $Y=58158
X5326 VSS VDD 340 286 323 326 AO21x1_ASAP7_75t_R $T=45144 56160 1 0 $X=45144 $Y=54992
X5327 VSS VDD 305 344 659 308 AO21x1_ASAP7_75t_R $T=46224 23760 0 0 $X=46224 $Y=23598
X5328 VSS VDD 371 336 321 348 AO21x1_ASAP7_75t_R $T=47736 47520 1 0 $X=47736 $Y=46352
X5329 VSS VDD 343 344 350 359 AO21x1_ASAP7_75t_R $T=49032 30240 1 0 $X=49032 $Y=29072
X5330 VSS VDD 391 326 362 363 AO21x1_ASAP7_75t_R $T=49248 47520 1 0 $X=49248 $Y=46352
X5331 VSS VDD 305 344 324 367 AO21x1_ASAP7_75t_R $T=49464 25920 1 0 $X=49464 $Y=24752
X5332 VSS VDD 559 321 405 418 AO21x1_ASAP7_75t_R $T=57888 32400 1 0 $X=57888 $Y=31232
X5333 VSS VDD 41 465 360 433 AO21x1_ASAP7_75t_R $T=60048 54000 1 0 $X=60048 $Y=52832
X5334 VSS VDD 340 353 429 438 AO21x1_ASAP7_75t_R $T=60480 25920 1 0 $X=60480 $Y=24752
X5361 116 VSS VDD 110 42 92 OA21x2_ASAP7_75t_R $T=7560 12960 0 0 $X=7560 $Y=12872
X5362 116 VSS VDD 108 112 89 OA21x2_ASAP7_75t_R $T=8208 19440 0 0 $X=8208 $Y=19352
X5363 116 VSS VDD 114 43 103 OA21x2_ASAP7_75t_R $T=8208 21600 0 0 $X=8208 $Y=21512
X5364 116 VSS VDD 113 127 115 OA21x2_ASAP7_75t_R $T=9072 12960 0 0 $X=9072 $Y=12872
X5365 168 VSS VDD 6 129 136 OA21x2_ASAP7_75t_R $T=12096 47520 1 0 $X=12096 $Y=46352
X5366 116 VSS VDD 134 139 155 OA21x2_ASAP7_75t_R $T=13608 12960 0 0 $X=13608 $Y=12872
X5367 141 VSS VDD 638 165 679 OA21x2_ASAP7_75t_R $T=14688 30240 1 0 $X=14688 $Y=29072
X5368 144 VSS VDD 660 73 158 OA21x2_ASAP7_75t_R $T=14904 41040 1 0 $X=14904 $Y=39872
X5369 116 VSS VDD 143 48 609 OA21x2_ASAP7_75t_R $T=15120 15120 0 0 $X=15120 $Y=15032
X5370 116 VSS VDD 147 46 151 OA21x2_ASAP7_75t_R $T=15120 23760 1 0 $X=15120 $Y=22592
X5371 116 VSS VDD 149 156 150 OA21x2_ASAP7_75t_R $T=15336 19440 0 0 $X=15336 $Y=19352
X5372 148 VSS VDD 145 73 640 OA21x2_ASAP7_75t_R $T=15336 36720 1 0 $X=15336 $Y=35552
X5373 205 VSS VDD 588 168 197 OA21x2_ASAP7_75t_R $T=26352 36720 1 0 $X=26352 $Y=35552
X5374 207 VSS VDD 641 236 204 OA21x2_ASAP7_75t_R $T=27432 54000 1 0 $X=27432 $Y=52832
X5375 137 VSS VDD 212 22 218 OA21x2_ASAP7_75t_R $T=28512 47520 0 0 $X=28512 $Y=47432
X5376 220 VSS VDD 233 73 235 OA21x2_ASAP7_75t_R $T=31536 43200 0 0 $X=31536 $Y=43112
X5377 3 VSS VDD 249 73 236 OA21x2_ASAP7_75t_R $T=31536 54000 1 0 $X=31536 $Y=52832
X5378 164 VSS VDD 252 259 262 OA21x2_ASAP7_75t_R $T=34992 54000 1 0 $X=34992 $Y=52832
X5379 271 VSS VDD 663 73 516 OA21x2_ASAP7_75t_R $T=37368 47520 1 0 $X=37368 $Y=46352
X5380 307 VSS VDD 340 308 532 OA21x2_ASAP7_75t_R $T=43416 23760 0 0 $X=43416 $Y=23672
X5381 282 VSS VDD 331 308 336 OA21x2_ASAP7_75t_R $T=46008 60480 0 0 $X=46008 $Y=60392
X5382 615 VSS VDD 423 669 346 OA21x2_ASAP7_75t_R $T=47304 12960 1 0 $X=47304 $Y=11792
X5383 317 VSS VDD 343 595 597 OA21x2_ASAP7_75t_R $T=47304 25920 0 0 $X=47304 $Y=25832
X5384 349 VSS VDD 423 356 318 OA21x2_ASAP7_75t_R $T=48816 15120 0 0 $X=48816 $Y=15032
X5385 536 VSS VDD 538 353 645 OA21x2_ASAP7_75t_R $T=48816 21600 0 0 $X=48816 $Y=21512
X5386 321 VSS VDD 371 73 546 OA21x2_ASAP7_75t_R $T=49032 41040 1 0 $X=49032 $Y=39872
X5387 358 VSS VDD 423 361 646 OA21x2_ASAP7_75t_R $T=49680 17280 0 0 $X=49680 $Y=17192
X5388 345 VSS VDD 369 353 550 OA21x2_ASAP7_75t_R $T=51624 30240 1 0 $X=51624 $Y=29072
X5389 548 VSS VDD 368 622 665 OA21x2_ASAP7_75t_R $T=52488 36720 0 0 $X=52488 $Y=36632
X5390 619 VSS VDD 423 384 386 OA21x2_ASAP7_75t_R $T=54000 12960 0 0 $X=54000 $Y=12872
X5391 393 VSS VDD 550 664 411 OA21x2_ASAP7_75t_R $T=54864 30240 1 0 $X=54864 $Y=29072
X5392 431 VSS VDD 423 601 564 OA21x2_ASAP7_75t_R $T=58536 12960 1 0 $X=58536 $Y=11792
X5393 391 VSS VDD 468 546 667 OA21x2_ASAP7_75t_R $T=58536 38880 1 0 $X=58536 $Y=37712
X5394 41 VSS VDD 593 667 670 OA21x2_ASAP7_75t_R $T=58968 36720 1 0 $X=58968 $Y=35552
X5395 563 VSS VDD 656 567 413 OA21x2_ASAP7_75t_R $T=58968 49680 1 0 $X=58968 $Y=48512
X5396 420 VSS VDD 423 416 426 OA21x2_ASAP7_75t_R $T=59184 23760 1 0 $X=59184 $Y=22592
X5397 569 VSS VDD 423 565 404 OA21x2_ASAP7_75t_R $T=59616 19440 0 0 $X=59616 $Y=19352
X5398 425 VSS VDD 423 568 620 OA21x2_ASAP7_75t_R $T=60048 15120 0 0 $X=60048 $Y=15032
X5411 VSS VDD 180 168 152 179 OAI21x1_ASAP7_75t_R $T=19872 54000 1 0 $X=19872 $Y=52832
X5412 VSS VDD 57 480 164 676 OAI21x1_ASAP7_75t_R $T=22680 30240 0 0 $X=22680 $Y=30086
X5413 VSS VDD 185 273 199 192 OAI21x1_ASAP7_75t_R $T=24192 49680 1 0 $X=24192 $Y=48512
X5414 VSS VDD 315 68 365 593 OAI21x1_ASAP7_75t_R $T=46008 36720 1 0 $X=46008 $Y=35552
X5415 VSS VDD 325 331 277 541 OAI21x1_ASAP7_75t_R $T=46224 51840 0 0 $X=46224 $Y=51686
X5416 VSS VDD 423 596 340 669 OAI21x1_ASAP7_75t_R $T=47088 12960 0 0 $X=47088 $Y=12806
X5417 VSS VDD 331 536 277 598 OAI21x1_ASAP7_75t_R $T=47520 54000 1 0 $X=47520 $Y=52832
X5418 VSS VDD 353 545 597 339 OAI21x1_ASAP7_75t_R $T=47736 28080 1 0 $X=47736 $Y=26912
X5419 VSS VDD 353 331 365 368 OAI21x1_ASAP7_75t_R $T=49680 36720 0 0 $X=49680 $Y=36566
X5420 VSS VDD 423 317 340 361 OAI21x1_ASAP7_75t_R $T=49896 19440 1 0 $X=49896 $Y=18272
X5421 VSS VDD 423 370 340 356 OAI21x1_ASAP7_75t_R $T=51192 15120 0 0 $X=51192 $Y=14966
X5422 VSS VDD 423 549 340 384 OAI21x1_ASAP7_75t_R $T=52272 12960 0 0 $X=52272 $Y=12806
X5423 VSS VDD 423 402 340 601 OAI21x1_ASAP7_75t_R $T=56592 12960 0 0 $X=56592 $Y=12806
X5424 VSS VDD 423 430 340 416 OAI21x1_ASAP7_75t_R $T=58536 21600 0 0 $X=58536 $Y=21446
X5425 VSS VDD 423 440 340 568 OAI21x1_ASAP7_75t_R $T=59616 15120 1 0 $X=59616 $Y=13952
X5426 VSS VDD 423 428 340 565 OAI21x1_ASAP7_75t_R $T=59832 19440 1 0 $X=59832 $Y=18272
X5437 VSS VDD ICV_67 $T=4752 25920 1 0 $X=4752 $Y=24752
X5438 VSS VDD ICV_67 $T=4752 25920 0 0 $X=4752 $Y=25832
X5439 VSS VDD ICV_67 $T=4752 34560 0 0 $X=4752 $Y=34472
X5440 VSS VDD ICV_67 $T=4752 36720 0 0 $X=4752 $Y=36632
X5441 VSS VDD ICV_67 $T=4752 41040 1 0 $X=4752 $Y=39872
X5442 VSS VDD ICV_67 $T=4752 45360 1 0 $X=4752 $Y=44192
X5443 VSS VDD ICV_67 $T=4752 45360 0 0 $X=4752 $Y=45272
X5444 VSS VDD ICV_67 $T=17496 49680 0 0 $X=17496 $Y=49592
X5445 VSS VDD ICV_67 $T=23760 10800 0 0 $X=23760 $Y=10712
X5446 VSS VDD ICV_67 $T=23760 51840 0 0 $X=23760 $Y=51752
X5447 VSS VDD ICV_67 $T=23760 60480 0 0 $X=23760 $Y=60392
X5448 VSS VDD ICV_67 $T=23760 64800 1 0 $X=23760 $Y=63632
X5449 VSS VDD ICV_67 $T=23760 66960 1 0 $X=23760 $Y=65792
X5450 VSS VDD ICV_67 $T=25920 25920 1 0 $X=25920 $Y=24752
X5451 VSS VDD ICV_67 $T=26784 28080 1 0 $X=26784 $Y=26912
X5452 VSS VDD ICV_67 $T=27216 38880 0 0 $X=27216 $Y=38792
X5453 VSS VDD ICV_67 $T=27864 25920 0 0 $X=27864 $Y=25832
X5454 VSS VDD ICV_67 $T=28728 17280 0 0 $X=28728 $Y=17192
X5455 VSS VDD ICV_67 $T=33912 15120 1 0 $X=33912 $Y=13952
X5456 VSS VDD ICV_67 $T=34128 12960 1 0 $X=34128 $Y=11792
X5457 VSS VDD ICV_67 $T=34344 30240 1 0 $X=34344 $Y=29072
X5458 VSS VDD ICV_67 $T=34344 41040 0 0 $X=34344 $Y=40952
X5459 VSS VDD ICV_67 $T=34560 43200 1 0 $X=34560 $Y=42032
X5460 VSS VDD ICV_67 $T=35208 30240 0 0 $X=35208 $Y=30152
X5461 VSS VDD ICV_67 $T=36072 19440 1 0 $X=36072 $Y=18272
X5462 VSS VDD ICV_67 $T=38448 32400 1 0 $X=38448 $Y=31232
X5463 VSS VDD ICV_67 $T=39096 17280 1 0 $X=39096 $Y=16112
X5464 VSS VDD ICV_67 $T=43848 60480 1 0 $X=43848 $Y=59312
X5465 VSS VDD ICV_67 $T=45792 6480 0 0 $X=45792 $Y=6392
X5466 VSS VDD ICV_67 $T=46224 8640 1 0 $X=46224 $Y=7472
X5467 VSS VDD ICV_67 $T=47736 32400 0 0 $X=47736 $Y=32312
X5468 VSS VDD ICV_67 $T=49248 15120 1 0 $X=49248 $Y=13952
X5469 VSS VDD ICV_67 $T=49248 54000 1 0 $X=49248 $Y=52832
X5470 VSS VDD ICV_67 $T=49896 54000 0 0 $X=49896 $Y=53912
X5471 VSS VDD ICV_67 $T=50112 30240 0 0 $X=50112 $Y=30152
X5472 VSS VDD ICV_67 $T=50544 41040 1 0 $X=50544 $Y=39872
X5473 VSS VDD ICV_67 $T=51408 28080 1 0 $X=51408 $Y=26912
X5474 VSS VDD ICV_67 $T=51408 34560 1 0 $X=51408 $Y=33392
X5475 VSS VDD ICV_67 $T=55080 8640 0 0 $X=55080 $Y=8552
X5476 VSS VDD ICV_67 $T=56376 8640 1 0 $X=56376 $Y=7472
X5477 VSS VDD ICV_67 $T=57024 6480 0 0 $X=57024 $Y=6392
X5478 VSS VDD ICV_67 $T=57456 17280 0 0 $X=57456 $Y=17192
X5479 VSS VDD ICV_67 $T=57672 10800 1 0 $X=57672 $Y=9632
X5480 VSS VDD ICV_67 $T=58968 69120 1 0 $X=58968 $Y=67952
X5481 VSS VDD ICV_67 $T=59184 60480 1 0 $X=59184 $Y=59312
X5490 VSS VDD 201 499 493 XOR2x2_ASAP7_75t_R $T=25056 32400 1 0 $X=25056 $Y=31232
X5491 VSS VDD 238 237 243 XOR2x2_ASAP7_75t_R $T=32184 62640 1 0 $X=32184 $Y=61472
X5492 VSS VDD 232 237 266 XOR2x2_ASAP7_75t_R $T=34776 62640 1 0 $X=34776 $Y=61472
X5493 VSS VDD 246 304 270 XOR2x2_ASAP7_75t_R $T=35208 43200 0 0 $X=35208 $Y=42946
X5494 VSS VDD 240 253 277 XOR2x2_ASAP7_75t_R $T=36504 54000 0 0 $X=36504 $Y=53746
X5495 VSS VDD 274 290 217 XOR2x2_ASAP7_75t_R $T=36720 49680 1 0 $X=36720 $Y=48512
X5496 VSS VDD 279 268 283 XOR2x2_ASAP7_75t_R $T=37152 62640 1 0 $X=37152 $Y=61472
X5497 VSS VDD 284 613 677 XOR2x2_ASAP7_75t_R $T=38448 47520 0 0 $X=38448 $Y=47266
X5498 VSS VDD 395 555 378 XOR2x2_ASAP7_75t_R $T=54216 45360 1 0 $X=54216 $Y=44192
X5499 VSS VDD 406 392 342 XOR2x2_ASAP7_75t_R $T=55728 43200 0 0 $X=55728 $Y=42946
X5500 VSS VDD 221 234 226 18 489 239 AND5x1_ASAP7_75t_R $T=32184 34560 1 0 $X=32184 $Y=33392
X5501 VSS VDD 385 555 618 452 400 390 AND5x1_ASAP7_75t_R $T=55512 38880 1 0 $X=55512 $Y=37712
X5502 VSS VDD 480 187 164 168 487 AOI211x1_ASAP7_75t_R $T=20088 28080 0 0 $X=20088 $Y=27992
X5503 VSS VDD 480 210 164 168 492 AOI211x1_ASAP7_75t_R $T=22680 28080 0 0 $X=22680 $Y=27992
X5504 VSS VDD 369 554 321 536 405 AOI211x1_ASAP7_75t_R $T=54864 32400 1 0 $X=54864 $Y=31232
X5505 VSS VDD ICV_69 $T=4752 30240 1 0 $X=4752 $Y=29072
X5506 VSS VDD ICV_69 $T=4752 32400 0 0 $X=4752 $Y=32312
X5507 VSS VDD ICV_69 $T=9288 23760 0 0 $X=9288 $Y=23672
X5508 VSS VDD ICV_69 $T=14256 45360 0 0 $X=14256 $Y=45272
X5509 VSS VDD ICV_69 $T=14256 51840 1 0 $X=14256 $Y=50672
X5510 VSS VDD ICV_69 $T=17496 43200 0 0 $X=17496 $Y=43112
X5511 VSS VDD ICV_69 $T=24408 45360 1 0 $X=24408 $Y=44192
X5512 VSS VDD ICV_69 $T=46224 58320 0 0 $X=46224 $Y=58232
X5513 VSS VDD ICV_69 $T=46440 56160 1 0 $X=46440 $Y=54992
X5514 VSS VDD ICV_69 $T=53784 23760 1 0 $X=53784 $Y=22592
X5523 VSS VDD 206 119 BUFx4_ASAP7_75t_R $T=16632 25920 0 0 $X=16632 $Y=25832
X5524 VSS VDD 206 177 BUFx4_ASAP7_75t_R $T=19656 47520 0 0 $X=19656 $Y=47432
X5525 VSS VDD 206 182 BUFx4_ASAP7_75t_R $T=25272 28080 0 0 $X=25272 $Y=27992
X5526 VSS VDD 206 16 BUFx4_ASAP7_75t_R $T=26352 49680 1 0 $X=26352 $Y=48512
X5527 VSS VDD clk 206 BUFx4_ASAP7_75t_R $T=34776 36720 1 0 $X=34776 $Y=35552
X5528 VSS VDD 206 27 BUFx4_ASAP7_75t_R $T=47304 21600 0 0 $X=47304 $Y=21512
X5529 VSS VDD 206 28 BUFx4_ASAP7_75t_R $T=47304 45360 0 0 $X=47304 $Y=45272
X5530 VSS VDD 206 374 BUFx4_ASAP7_75t_R $T=51192 41040 0 0 $X=51192 $Y=40952
X5531 VSS VDD 206 381 BUFx4_ASAP7_75t_R $T=52056 25920 1 0 $X=52056 $Y=24752
X5532 VSS VDD 7 168 130 626 OR3x1_ASAP7_75t_R $T=11448 32400 0 0 $X=11448 $Y=32206
X5533 VSS VDD 7 137 136 135 OR3x1_ASAP7_75t_R $T=13176 49680 1 0 $X=13176 $Y=48512
X5534 VSS VDD 168 109 679 654 OR3x1_ASAP7_75t_R $T=14472 28080 0 0 $X=14472 $Y=27886
X5535 VSS VDD 142 140 638 152 OR3x1_ASAP7_75t_R $T=14472 32400 0 0 $X=14472 $Y=32206
X5536 VSS VDD 171 168 109 484 OR3x1_ASAP7_75t_R $T=15984 32400 1 0 $X=15984 $Y=31232
X5537 VSS VDD 7 164 173 116 OR3x1_ASAP7_75t_R $T=18144 28080 0 0 $X=18144 $Y=27886
X5538 VSS VDD 11 175 179 181 OR3x1_ASAP7_75t_R $T=20304 56160 0 0 $X=20304 $Y=55966
X5539 VSS VDD 202 205 588 610 OR3x1_ASAP7_75t_R $T=25056 34560 0 0 $X=25056 $Y=34366
X5540 VSS VDD 23 630 179 215 OR3x1_ASAP7_75t_R $T=27432 56160 1 0 $X=27432 $Y=54992
X5541 VSS VDD 229 273 591 655 OR3x1_ASAP7_75t_R $T=32616 30240 0 0 $X=32616 $Y=30046
X5542 VSS VDD 254 164 258 241 OR3x1_ASAP7_75t_R $T=35208 54000 0 0 $X=35208 $Y=53806
X5543 VSS VDD 511 259 242 24 OR3x1_ASAP7_75t_R $T=35424 60480 0 0 $X=35424 $Y=60286
X5544 VSS VDD 225 268 251 232 OR3x1_ASAP7_75t_R $T=35424 64800 0 0 $X=35424 $Y=64606
X5545 VSS VDD 507 273 239 662 OR3x1_ASAP7_75t_R $T=35856 34560 1 0 $X=35856 $Y=33392
X5546 VSS VDD 642 3 262 512 OR3x1_ASAP7_75t_R $T=36504 54000 1 0 $X=36504 $Y=52832
X5547 VSS VDD 611 237 268 240 OR3x1_ASAP7_75t_R $T=36720 62640 0 0 $X=36720 $Y=62446
X5548 VSS VDD 254 245 521 280 OR3x1_ASAP7_75t_R $T=37800 56160 1 0 $X=37800 $Y=54992
X5549 VSS VDD 164 258 25 520 OR3x1_ASAP7_75t_R $T=38448 38880 1 0 $X=38448 $Y=37712
X5550 VSS VDD 164 258 280 274 OR3x1_ASAP7_75t_R $T=38664 49680 0 0 $X=38664 $Y=49486
X5551 VSS VDD 259 252 280 294 OR3x1_ASAP7_75t_R $T=40176 56160 1 0 $X=40176 $Y=54992
X5552 VSS VDD 164 258 524 525 OR3x1_ASAP7_75t_R $T=40392 36720 0 0 $X=40392 $Y=36526
X5553 VSS VDD 259 252 524 295 OR3x1_ASAP7_75t_R $T=40608 54000 1 0 $X=40608 $Y=52832
X5554 VSS VDD 259 254 252 299 OR3x1_ASAP7_75t_R $T=40824 58320 1 0 $X=40824 $Y=57152
X5555 VSS VDD 319 68 339 527 OR3x1_ASAP7_75t_R $T=41472 25920 0 0 $X=41472 $Y=25726
X5556 VSS VDD 306 288 524 25 OR3x1_ASAP7_75t_R $T=41472 38880 0 0 $X=41472 $Y=38686
X5557 VSS VDD 290 280 24 303 OR3x1_ASAP7_75t_R $T=41472 49680 1 0 $X=41472 $Y=48512
X5558 VSS VDD 254 245 24 530 OR3x1_ASAP7_75t_R $T=42552 60480 1 0 $X=42552 $Y=59312
X5559 VSS VDD 306 524 24 309 OR3x1_ASAP7_75t_R $T=42768 36720 0 0 $X=42768 $Y=36526
X5560 VSS VDD 536 340 266 325 OR3x1_ASAP7_75t_R $T=44928 51840 0 0 $X=44928 $Y=51646
X5561 VSS VDD 301 338 337 595 OR3x1_ASAP7_75t_R $T=47088 28080 0 0 $X=47088 $Y=27886
X5562 VSS VDD 317 331 41 372 OR3x1_ASAP7_75t_R $T=51192 28080 0 0 $X=51192 $Y=27886
X5563 VSS VDD 180 168 21 219 248 658 AO32x1_ASAP7_75t_R $T=30024 54000 0 0 $X=30024 $Y=53866
X5564 VSS VDD 255 168 73 526 514 230 AO32x1_ASAP7_75t_R $T=36288 32400 0 0 $X=36288 $Y=32266
X5565 VSS VDD 329 351 338 316 345 594 AO32x1_ASAP7_75t_R $T=46872 36720 0 0 $X=46872 $Y=36586
X5566 VSS VDD 311 351 338 543 345 540 AO32x1_ASAP7_75t_R $T=47088 56160 0 0 $X=47088 $Y=56026
X5567 VSS VDD 298 351 338 278 345 544 AO32x1_ASAP7_75t_R $T=49032 58320 1 0 $X=49032 $Y=57152
X5568 VSS VDD 543 351 338 298 345 355 AO32x1_ASAP7_75t_R $T=49248 56160 0 0 $X=49248 $Y=56026
X5569 VSS VDD 396 41 73 600 594 364 AO32x1_ASAP7_75t_R $T=50328 34560 0 0 $X=50328 $Y=34426
X5570 VSS VDD 119 INVx3_ASAP7_75t_R $T=16632 25920 1 0 $X=16632 $Y=24752
X5571 VSS VDD 374 INVx3_ASAP7_75t_R $T=49896 43200 1 0 $X=49896 $Y=42032
X5572 VSS VDD 232 164 238 OR2x2_ASAP7_75t_R $T=32400 62640 0 0 $X=32400 $Y=62552
X5573 VSS VDD 240 164 247 OR2x2_ASAP7_75t_R $T=33696 54000 1 0 $X=33696 $Y=52832
X5574 VSS VDD 24 164 260 OR2x2_ASAP7_75t_R $T=34992 58320 0 0 $X=34992 $Y=58232
X5575 VSS VDD 252 259 258 OR2x2_ASAP7_75t_R $T=39312 54000 1 0 $X=39312 $Y=52832
X5576 VSS VDD 25 258 289 OR2x2_ASAP7_75t_R $T=39528 38880 0 0 $X=39528 $Y=38792
X5577 VSS VDD 164 611 279 OR2x2_ASAP7_75t_R $T=39528 62640 1 0 $X=39528 $Y=61472
X5578 VSS VDD 285 7 291 OR2x2_ASAP7_75t_R $T=39744 17280 0 0 $X=39744 $Y=17192
X5579 VSS VDD 324 317 659 OR2x2_ASAP7_75t_R $T=46008 21600 0 0 $X=46008 $Y=21512
X5580 VSS VDD 545 343 331 OR2x2_ASAP7_75t_R $T=49896 28080 0 0 $X=49896 $Y=27992
X5592 VSS VDD ICV_72 $T=4752 10800 0 0 $X=4752 $Y=10712
X5593 VSS VDD ICV_72 $T=9720 17280 0 0 $X=9720 $Y=17192
X5594 VSS VDD ICV_72 $T=14256 60480 1 0 $X=14256 $Y=59312
X5595 VSS VDD ICV_72 $T=14904 15120 1 0 $X=14904 $Y=13952
X5596 VSS VDD ICV_72 $T=15120 12960 1 0 $X=15120 $Y=11792
X5597 VSS VDD ICV_72 $T=16632 23760 1 0 $X=16632 $Y=22592
X5598 VSS VDD ICV_72 $T=16848 21600 0 0 $X=16848 $Y=21512
X5599 VSS VDD ICV_72 $T=17064 19440 1 0 $X=17064 $Y=18272
X5600 VSS VDD ICV_72 $T=17928 15120 0 0 $X=17928 $Y=15032
X5601 VSS VDD ICV_72 $T=18144 19440 0 0 $X=18144 $Y=19352
X5602 VSS VDD ICV_72 $T=18792 17280 1 0 $X=18792 $Y=16112
X5603 VSS VDD ICV_72 $T=19008 21600 1 0 $X=19008 $Y=20432
X5604 VSS VDD ICV_72 $T=19008 23760 0 0 $X=19008 $Y=23672
X5605 VSS VDD ICV_72 $T=19440 12960 0 0 $X=19440 $Y=12872
X5606 VSS VDD ICV_72 $T=20952 51840 1 0 $X=20952 $Y=50672
X5607 VSS VDD ICV_72 $T=28944 41040 1 0 $X=28944 $Y=39872
X5608 VSS VDD ICV_72 $T=39960 69120 1 0 $X=39960 $Y=67952
X5609 VSS VDD ICV_72 $T=40392 66960 0 0 $X=40392 $Y=66872
X5610 VSS VDD ICV_72 $T=40824 62640 1 0 $X=40824 $Y=61472
X5611 VSS VDD ICV_72 $T=40824 66960 1 0 $X=40824 $Y=65792
X5612 VSS VDD ICV_72 $T=41256 64800 0 0 $X=41256 $Y=64712
X5613 VSS VDD ICV_72 $T=42768 4320 0 0 $X=42768 $Y=4232
X5614 VSS VDD ICV_72 $T=45576 6480 1 0 $X=45576 $Y=5312
X5615 VSS VDD ICV_72 $T=47520 60480 0 0 $X=47520 $Y=60392
X5616 VSS VDD 124 106 120 85 AOI21x1_ASAP7_75t_R $T=9072 28080 0 0 $X=9072 $Y=27926
X5617 VSS VDD 172 157 273 144 AOI21x1_ASAP7_75t_R $T=13608 38880 0 0 $X=13608 $Y=38726
X5618 VSS VDD 7 163 189 169 AOI21x1_ASAP7_75t_R $T=22248 54000 1 0 $X=22248 $Y=52832
X5619 VSS VDD 12 14 489 190 AOI21x1_ASAP7_75t_R $T=22680 56160 1 0 $X=22680 $Y=54992
X5620 VSS VDD 661 203 192 184 AOI21x1_ASAP7_75t_R $T=22896 45360 0 0 $X=22896 $Y=45206
X5621 VSS VDD 223 168 211 233 AOI21x1_ASAP7_75t_R $T=31104 41040 0 0 $X=31104 $Y=40886
X5622 VSS VDD 7 229 591 501 AOI21x1_ASAP7_75t_R $T=32616 30240 1 0 $X=32616 $Y=29072
X5623 VSS VDD 7 527 302 515 AOI21x1_ASAP7_75t_R $T=39096 23760 0 0 $X=39096 $Y=23606
X5624 VSS VDD 7 65 292 523 AOI21x1_ASAP7_75t_R $T=40392 28080 1 0 $X=40392 $Y=26912
X5625 VSS VDD 340 324 538 614 AOI21x1_ASAP7_75t_R $T=46872 21600 1 0 $X=46872 $Y=20432
X5626 VSS VDD 659 305 344 536 AOI21x1_ASAP7_75t_R $T=47520 23760 0 0 $X=47520 $Y=23606
X5627 VSS VDD 504 521 248 XNOR2x1_ASAP7_75t_R $T=33048 56160 1 0 $X=33048 $Y=54992
X5628 VSS VDD 530 521 332 XNOR2x1_ASAP7_75t_R $T=42336 58320 0 0 $X=42336 $Y=58138
X5629 VSS VDD 303 613 533 XNOR2x1_ASAP7_75t_R $T=42984 49680 1 0 $X=42984 $Y=48512
X5630 VSS VDD 309 288 329 XNOR2x1_ASAP7_75t_R $T=44064 36720 0 0 $X=44064 $Y=36538
X5631 VSS VDD 289 26 330 XNOR2x1_ASAP7_75t_R $T=44928 38880 0 0 $X=44928 $Y=38698
X5632 VSS VDD 194 491 51 205 480 173 OR5x1_ASAP7_75t_R $T=23760 38880 0 0 $X=23760 $Y=38670
X5633 VSS VDD 611 253 511 237 268 252 OR5x1_ASAP7_75t_R $T=34776 60480 1 0 $X=34776 $Y=59312
X5634 VSS VDD 253 225 237 268 251 242 OR5x1_ASAP7_75t_R $T=34992 62640 0 0 $X=34992 $Y=62430
X5635 VSS VDD 254 245 613 521 290 524 OR5x1_ASAP7_75t_R $T=39744 54000 0 0 $X=39744 $Y=53790
X5636 VSS VDD 26 261 258 25 331 315 OR5x1_ASAP7_75t_R $T=43200 38880 0 0 $X=43200 $Y=38670
X5637 VSS VDD ICV_73 $T=4752 41040 0 0 $X=4752 $Y=40952
X5638 VSS VDD ICV_73 $T=4752 49680 0 0 $X=4752 $Y=49592
X5639 VSS VDD ICV_73 $T=4752 51840 0 0 $X=4752 $Y=51752
X5640 VSS VDD ICV_73 $T=4752 54000 0 0 $X=4752 $Y=53912
X5641 VSS VDD ICV_73 $T=4752 56160 0 0 $X=4752 $Y=56072
X5642 VSS VDD ICV_73 $T=4752 58320 0 0 $X=4752 $Y=58232
X5643 VSS VDD ICV_73 $T=23760 66960 0 0 $X=23760 $Y=66872
X5644 VSS VDD ICV_73 $T=39960 49680 0 0 $X=39960 $Y=49592
X5645 VSS VDD ICV_74 $T=4752 4320 0 0 $X=4752 $Y=4232
X5646 VSS VDD ICV_74 $T=4752 6480 0 0 $X=4752 $Y=6392
X5647 VSS VDD ICV_74 $T=4752 8640 0 0 $X=4752 $Y=8552
X5648 VSS VDD ICV_74 $T=4752 60480 0 0 $X=4752 $Y=60392
X5649 VSS VDD ICV_74 $T=4752 62640 0 0 $X=4752 $Y=62552
X5650 VSS VDD ICV_74 $T=4752 64800 0 0 $X=4752 $Y=64712
X5651 VSS VDD ICV_74 $T=4752 66960 0 0 $X=4752 $Y=66872
X5652 VSS VDD ICV_74 $T=23760 4320 0 0 $X=23760 $Y=4232
X5653 VSS VDD ICV_74 $T=23760 6480 0 0 $X=23760 $Y=6392
X5654 VSS VDD ICV_74 $T=23760 8640 0 0 $X=23760 $Y=8552
X5655 VSS VDD ICV_74 $T=40392 62640 0 0 $X=40392 $Y=62552
X5656 VSS VDD 177 CKINVDCx6p67_ASAP7_75t_R $T=19656 47520 1 0 $X=19656 $Y=46352
X5657 VSS VDD 182 CKINVDCx6p67_ASAP7_75t_R $T=24624 30240 1 0 $X=24624 $Y=29072
.ENDS
***************************************
