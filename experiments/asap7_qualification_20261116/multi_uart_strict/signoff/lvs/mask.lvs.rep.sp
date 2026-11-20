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
.SUBCKT FILLERxp5_ASAP7_75t_R
** N=4 EP=0 IP=0 FDC=0
*.CALIBRE ISOLATED NETS: VSS VDD
.ENDS
***************************************
.SUBCKT FILLER_ASAP7_75t_R
** N=6 EP=0 IP=0 FDC=0
*.CALIBRE ISOLATED NETS: VSS VDD
.ENDS
***************************************
.SUBCKT ICV_4
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_5
** N=2 EP=0 IP=4 FDC=0
.ENDS
***************************************
.SUBCKT DECAPx1_ASAP7_75t_R VSS VDD
** N=8 EP=2 IP=0 FDC=2
M0 VSS 4 3 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=500 $Y=108 $D=1
M1 4 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_6 1 2
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
.SUBCKT ICV_7
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_8 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=4
X1 1 2 DECAPx2_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_9 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=4
X1 1 2 DECAPx2_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_10 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=4
X1 1 2 DECAPx2_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
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
.SUBCKT ICV_11
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_12 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT INVx1_ASAP7_75t_R VSS VDD A Y
** N=8 EP=4 IP=0 FDC=2
M0 Y A VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=108 $D=1
M1 Y A VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=284 $Y=648 $D=0
.ENDS
***************************************
.SUBCKT ICV_13
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_14 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_15 1 2 3 4
*.CALIBRE ICV_CELL 9
** N=4 EP=4 IP=6 FDC=2
X1 1 2 3 4 INVx1_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_16 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_17
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
.SUBCKT ICV_18
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_19 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=12
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_20 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=14
X0 1 2 DECAPx1_ASAP7_75t_R $T=3024 0 0 0 $X=3024 $Y=-88
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_21
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_22
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
.SUBCKT VIA_VIA12_DOWN
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_23
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_24
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_25 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=28
X0 1 2 DECAPx4_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_26
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT VIA_VIA45
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT VIA_VIA56
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
.SUBCKT ICV_27 1 2 3 4 5
*.CALIBRE ICV_CELL 9
** N=5 EP=5 IP=7 FDC=24
X1 1 2 3 4 5 DFFHQNx1_ASAP7_75t_R $T=432 0 0 0 $X=432 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_28
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_29
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_30
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_31
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_32
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_33
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_34
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
.SUBCKT ICV_35 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=32
X0 1 2 DECAPx6_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
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
.SUBCKT ICV_36 1 2 3 4 5
*.CALIBRE ICV_CELL 9
** N=5 EP=5 IP=7 FDC=24
X1 1 2 3 4 5 DFFHQNx1_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-88
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
.SUBCKT ICV_40
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_41
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_42
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_43
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_44
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_45
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_46
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_47
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_48 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=40
X0 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
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
.SUBCKT ICV_49
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_50
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_51
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
.SUBCKT ICV_52
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
.SUBCKT ICV_53
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_54
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_55
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_56
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_57 1 2 3 4 5 6
*.CALIBRE ICV_CELL 9
** N=6 EP=6 IP=8 FDC=8
X1 1 2 3 4 5 6 AND3x1_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-210
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
.SUBCKT ICV_58
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_59
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_60
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_61
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_62
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_63
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_64
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_65
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_66
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT VIA_VIA67
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
.SUBCKT ICV_67 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=80
X0 1 2 ICV_48 $T=0 0 0 0 $X=0 $Y=-88
X1 1 2 ICV_48 $T=0 2160 1 0 $X=0 $Y=992
.ENDS
***************************************
.SUBCKT VIA_via1_2_14040_18_1_390_36_36
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_68 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=80
X1 1 2 ICV_67 $T=432 0 0 0 $X=432 $Y=-88
.ENDS
***************************************
.SUBCKT uart VSS VDD tx_busy rst prescale[1] s_axis_tready s_axis_tdata[7] s_axis_tdata[5] prescale[7] prescale[10] prescale[8] clk rxd rx_busy m_axis_tready m_axis_tdata[4] rx_overrun_error m_axis_tdata[2] m_axis_tdata[1] m_axis_tdata[0]
+ prescale[4] s_axis_tdata[6] s_axis_tdata[4] prescale[9] s_axis_tdata[0] s_axis_tdata[3] prescale[12] prescale[14] m_axis_tdata[6] m_axis_tdata[5] rx_frame_error s_axis_tdata[1] m_axis_tdata[3] prescale[3] prescale[5] prescale[2] prescale[0] s_axis_tvalid prescale[11] txd
+ prescale[6] prescale[15] prescale[13] s_axis_tdata[2] m_axis_tdata[7] m_axis_tvalid
** N=764 EP=46 IP=4572 FDC=12999
M0 23 3 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=17564 $Y=41148 $D=1
M1 VSS 3 23 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=17780 $Y=41148 $D=1
M2 695 4 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=17996 $Y=41148 $D=1
M3 696 5 695 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=18212 $Y=41148 $D=1
M4 697 6 696 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=18428 $Y=41148 $D=1
M5 3 7 697 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=18644 $Y=41148 $D=1
M6 59 8 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=19508 $Y=38988 $D=1
M7 693 10 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19724 $Y=21708 $D=1
M8 50 9 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19724 $Y=29808 $D=1
M9 VSS 8 59 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=19724 $Y=38988 $D=1
M10 VSS 9 50 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19940 $Y=29808 $D=1
M11 11 11 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19940 $Y=38988 $D=1
M12 VSS 11 11 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20156 $Y=38988 $D=1
M13 55 12 693 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20372 $Y=21708 $D=1
M14 11 13 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20372 $Y=38988 $D=1
M15 694 14 55 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20588 $Y=21708 $D=1
M16 50 15 51 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20588 $Y=29808 $D=1
M17 VSS 13 11 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20588 $Y=38988 $D=1
M18 51 15 50 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20804 $Y=29808 $D=1
M19 59 8 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=20804 $Y=38988 $D=1
M20 53 17 51 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21020 $Y=29808 $D=1
M21 24 19 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21020 $Y=34668 $D=1
M22 VSS 8 59 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=21020 $Y=38988 $D=1
M23 VSS 16 694 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=21708 $D=1
M24 51 17 53 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=29808 $D=1
M25 VSS 19 24 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=34668 $D=1
M26 18 18 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=38988 $D=1
M27 24 19 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21452 $Y=34668 $D=1
M28 VSS 18 18 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21452 $Y=38988 $D=1
M29 VSS 19 24 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21668 $Y=34668 $D=1
M30 18 20 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21668 $Y=38988 $D=1
M31 VSS 21 55 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=21884 $Y=21708 $D=1
M32 53 22 54 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21884 $Y=29808 $D=1
M33 56 23 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21884 $Y=34668 $D=1
M34 VSS 20 18 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21884 $Y=38988 $D=1
M35 54 22 53 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22100 $Y=29808 $D=1
M36 VSS 23 56 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22100 $Y=34668 $D=1
M37 59 8 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=22100 $Y=38988 $D=1
M38 28 24 54 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22316 $Y=29808 $D=1
M39 VSS 8 59 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=22316 $Y=38988 $D=1
M40 54 24 28 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22532 $Y=29808 $D=1
M41 27 25 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22532 $Y=38988 $D=1
M42 56 26 57 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22748 $Y=34668 $D=1
M43 VSS 25 27 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22748 $Y=38988 $D=1
M44 57 26 56 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22964 $Y=34668 $D=1
M45 27 27 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22964 $Y=38988 $D=1
M46 58 28 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23180 $Y=29808 $D=1
M47 19 29 57 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23180 $Y=34668 $D=1
M48 VSS 27 27 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23180 $Y=38988 $D=1
M49 VSS 28 58 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23396 $Y=29808 $D=1
M50 57 29 19 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23396 $Y=34668 $D=1
M51 59 8 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=23396 $Y=38988 $D=1
M52 VSS 8 59 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=23612 $Y=38988 $D=1
M53 59 8 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=23828 $Y=38988 $D=1
M54 VSS 8 59 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=24044 $Y=38988 $D=1
M55 62 30 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=29444 $Y=47088 $D=1
M56 VSS 31 62 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=29660 $Y=47088 $D=1
M57 63 32 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=29876 $Y=38988 $D=1
M58 VSS 32 63 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=30092 $Y=38988 $D=1
M59 33 33 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30308 $Y=38988 $D=1
M60 62 34 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30308 $Y=47088 $D=1
M61 VSS 33 33 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30524 $Y=38988 $D=1
M62 33 35 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30740 $Y=38988 $D=1
M63 VSS 35 33 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30956 $Y=38988 $D=1
M64 63 32 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=31172 $Y=38988 $D=1
M65 VSS 32 63 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=31388 $Y=38988 $D=1
M66 36 36 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31604 $Y=38988 $D=1
M67 VSS 36 36 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31820 $Y=38988 $D=1
M68 36 37 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32036 $Y=38988 $D=1
M69 VSS 37 36 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32252 $Y=38988 $D=1
M70 63 32 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=32468 $Y=38988 $D=1
M71 VSS 32 63 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=32684 $Y=38988 $D=1
M72 39 38 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32900 $Y=38988 $D=1
M73 VSS 38 39 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33116 $Y=38988 $D=1
M74 39 39 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33332 $Y=38988 $D=1
M75 VSS 39 39 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33548 $Y=38988 $D=1
M76 63 32 VSS VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=33764 $Y=38988 $D=1
M77 VSS 32 63 VSS nmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=33980 $Y=38988 $D=1
M78 698 40 65 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=36356 $Y=36828 $D=1
M79 VSS 41 698 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=36572 $Y=36828 $D=1
M80 699 41 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=36788 $Y=36828 $D=1
M81 65 40 699 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37004 $Y=36828 $D=1
M82 700 42 65 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37220 $Y=36828 $D=1
M83 VSS 43 700 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37436 $Y=36828 $D=1
M84 701 43 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37652 $Y=36828 $D=1
M85 65 42 701 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37868 $Y=36828 $D=1
M86 VSS 44 45 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41540 $Y=23328 $D=1
M87 66 45 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41756 $Y=23328 $D=1
M88 VSS 45 66 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41972 $Y=23328 $D=1
M89 66 45 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42188 $Y=23328 $D=1
M90 47 46 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43052 $Y=47628 $D=1
M91 VSS 46 47 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43268 $Y=47628 $D=1
M92 47 46 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43484 $Y=47628 $D=1
M93 VSS 46 47 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43700 $Y=47628 $D=1
M94 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43916 $Y=47628 $D=1
M95 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44132 $Y=47628 $D=1
M96 68 48 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44348 $Y=42768 $D=1
M97 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44348 $Y=47628 $D=1
M98 VSS 48 68 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44564 $Y=42768 $D=1
M99 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44564 $Y=47628 $D=1
M100 68 48 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44780 $Y=42768 $D=1
M101 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44780 $Y=47628 $D=1
M102 VSS 48 68 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44996 $Y=42768 $D=1
M103 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44996 $Y=47628 $D=1
M104 68 48 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45212 $Y=42768 $D=1
M105 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45212 $Y=47628 $D=1
M106 VSS 48 68 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45428 $Y=42768 $D=1
M107 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45428 $Y=47628 $D=1
M108 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45644 $Y=47628 $D=1
M109 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45860 $Y=47628 $D=1
M110 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46076 $Y=47628 $D=1
M111 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46292 $Y=47628 $D=1
M112 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46508 $Y=47628 $D=1
M113 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46724 $Y=47628 $D=1
M114 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46940 $Y=47628 $D=1
M115 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47156 $Y=47628 $D=1
M116 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47372 $Y=47628 $D=1
M117 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47588 $Y=47628 $D=1
M118 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47804 $Y=47628 $D=1
M119 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48020 $Y=47628 $D=1
M120 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48236 $Y=47628 $D=1
M121 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48452 $Y=47628 $D=1
M122 67 47 VSS VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48668 $Y=47628 $D=1
M123 VSS 47 67 VSS nmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48884 $Y=47628 $D=1
M124 23 3 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=17564 $Y=41688 $D=0
M125 VDD 3 23 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=17780 $Y=41688 $D=0
M126 3 4 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=17996 $Y=41688 $D=0
M127 VDD 5 3 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=18212 $Y=41688 $D=0
M128 3 6 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=18428 $Y=41688 $D=0
M129 VDD 7 3 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=18644 $Y=41688 $D=0
M130 59 8 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=19508 $Y=39636 $D=0
M131 VDD 10 49 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19724 $Y=22248 $D=0
M132 VDD 8 59 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=19724 $Y=39636 $D=0
M133 49 10 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19940 $Y=22248 $D=0
M134 28 9 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19940 $Y=29268 $D=0
M135 13 11 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=19940 $Y=39528 $D=0
M136 VDD 12 49 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20156 $Y=22248 $D=0
M137 VDD 11 13 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20156 $Y=39528 $D=0
M138 49 12 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20372 $Y=22248 $D=0
M139 13 13 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20372 $Y=39528 $D=0
M140 52 14 49 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20588 $Y=22248 $D=0
M141 VDD 15 28 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20588 $Y=29268 $D=0
M142 VDD 13 13 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20588 $Y=39528 $D=0
M143 49 14 52 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=20804 $Y=22248 $D=0
M144 59 8 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=20804 $Y=39636 $D=0
M145 52 16 49 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21020 $Y=22248 $D=0
M146 24 19 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21020 $Y=35208 $D=0
M147 VDD 8 59 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=21020 $Y=39636 $D=0
M148 49 16 52 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=22248 $D=0
M149 VDD 17 28 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=29268 $D=0
M150 VDD 19 24 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=35208 $D=0
M151 20 18 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21236 $Y=39528 $D=0
M152 24 19 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21452 $Y=35208 $D=0
M153 VDD 18 20 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21452 $Y=39528 $D=0
M154 VDD 19 24 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21668 $Y=35208 $D=0
M155 20 20 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21668 $Y=39528 $D=0
M156 55 21 52 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21884 $Y=22248 $D=0
M157 VDD 22 28 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21884 $Y=29268 $D=0
M158 19 23 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21884 $Y=35208 $D=0
M159 VDD 20 20 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=21884 $Y=39528 $D=0
M160 52 21 55 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22100 $Y=22248 $D=0
M161 59 8 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=22100 $Y=39636 $D=0
M162 VDD 8 59 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=22316 $Y=39636 $D=0
M163 VDD 24 28 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22532 $Y=29268 $D=0
M164 25 25 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22532 $Y=39528 $D=0
M165 VDD 26 19 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22748 $Y=35208 $D=0
M166 VDD 25 25 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22748 $Y=39528 $D=0
M167 25 27 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=22964 $Y=39528 $D=0
M168 58 28 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23180 $Y=29268 $D=0
M169 VDD 27 25 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23180 $Y=39528 $D=0
M170 VDD 28 58 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23396 $Y=29268 $D=0
M171 VDD 29 19 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=23396 $Y=35208 $D=0
M172 59 8 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=23396 $Y=39636 $D=0
M173 VDD 8 59 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=23612 $Y=39636 $D=0
M174 59 8 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=23828 $Y=39636 $D=0
M175 VDD 8 59 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=24044 $Y=39636 $D=0
M176 60 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=29012 $Y=46548 $D=0
M177 VDD 30 60 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=29228 $Y=46548 $D=0
M178 60 30 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=29444 $Y=46548 $D=0
M179 61 31 60 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=29660 $Y=46548 $D=0
M180 63 32 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=29876 $Y=39636 $D=0
M181 60 31 61 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=29876 $Y=46548 $D=0
M182 VDD 32 63 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=30092 $Y=39636 $D=0
M183 61 31 60 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30092 $Y=46548 $D=0
M184 35 33 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30308 $Y=39528 $D=0
M185 62 34 61 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30308 $Y=46548 $D=0
M186 VDD 33 35 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30524 $Y=39528 $D=0
M187 61 34 62 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30524 $Y=46548 $D=0
M188 35 35 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30740 $Y=39528 $D=0
M189 62 34 61 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30740 $Y=46548 $D=0
M190 VDD 35 35 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=30956 $Y=39528 $D=0
M191 63 32 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=31172 $Y=39636 $D=0
M192 VDD 32 63 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=31388 $Y=39636 $D=0
M193 37 36 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31604 $Y=39528 $D=0
M194 VDD 36 37 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=31820 $Y=39528 $D=0
M195 37 37 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32036 $Y=39528 $D=0
M196 VDD 37 37 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32252 $Y=39528 $D=0
M197 63 32 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=32468 $Y=39636 $D=0
M198 VDD 32 63 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=32684 $Y=39636 $D=0
M199 38 38 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=32900 $Y=39528 $D=0
M200 VDD 38 38 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33116 $Y=39528 $D=0
M201 38 39 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33332 $Y=39528 $D=0
M202 VDD 39 38 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=33548 $Y=39528 $D=0
M203 63 32 VDD VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=33764 $Y=39636 $D=0
M204 VDD 32 63 VDD pmos_rvt L=2e-08 W=5.4e-08 nfin=2 $X=33980 $Y=39636 $D=0
M205 VDD 40 64 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=36356 $Y=37368 $D=0
M206 64 41 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=36572 $Y=37368 $D=0
M207 VDD 41 64 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=36788 $Y=37368 $D=0
M208 64 40 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37004 $Y=37368 $D=0
M209 65 42 64 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37220 $Y=37368 $D=0
M210 64 43 65 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37436 $Y=37368 $D=0
M211 65 43 64 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37652 $Y=37368 $D=0
M212 64 42 65 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=37868 $Y=37368 $D=0
M213 VDD 44 45 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41540 $Y=22788 $D=0
M214 66 45 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41756 $Y=22788 $D=0
M215 VDD 45 66 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=41972 $Y=22788 $D=0
M216 66 45 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=42188 $Y=22788 $D=0
M217 47 46 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43052 $Y=48168 $D=0
M218 VDD 46 47 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43268 $Y=48168 $D=0
M219 47 46 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43484 $Y=48168 $D=0
M220 VDD 46 47 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43700 $Y=48168 $D=0
M221 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=43916 $Y=48168 $D=0
M222 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44132 $Y=48168 $D=0
M223 68 48 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44348 $Y=42228 $D=0
M224 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44348 $Y=48168 $D=0
M225 VDD 48 68 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44564 $Y=42228 $D=0
M226 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44564 $Y=48168 $D=0
M227 68 48 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44780 $Y=42228 $D=0
M228 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44780 $Y=48168 $D=0
M229 VDD 48 68 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44996 $Y=42228 $D=0
M230 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=44996 $Y=48168 $D=0
M231 68 48 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45212 $Y=42228 $D=0
M232 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45212 $Y=48168 $D=0
M233 VDD 48 68 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45428 $Y=42228 $D=0
M234 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45428 $Y=48168 $D=0
M235 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45644 $Y=48168 $D=0
M236 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=45860 $Y=48168 $D=0
M237 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46076 $Y=48168 $D=0
M238 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46292 $Y=48168 $D=0
M239 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46508 $Y=48168 $D=0
M240 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46724 $Y=48168 $D=0
M241 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=46940 $Y=48168 $D=0
M242 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47156 $Y=48168 $D=0
M243 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47372 $Y=48168 $D=0
M244 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47588 $Y=48168 $D=0
M245 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=47804 $Y=48168 $D=0
M246 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48020 $Y=48168 $D=0
M247 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48236 $Y=48168 $D=0
M248 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48452 $Y=48168 $D=0
M249 67 47 VDD VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48668 $Y=48168 $D=0
M250 VDD 47 67 VDD pmos_rvt L=2e-08 W=8.1e-08 nfin=3 $X=48884 $Y=48168 $D=0
X3120 VSS VDD DECAPx1_ASAP7_75t_R $T=6912 43200 0 0 $X=6912 $Y=43112
X3121 VSS VDD DECAPx1_ASAP7_75t_R $T=9504 17280 0 0 $X=9504 $Y=17192
X3122 VSS VDD DECAPx1_ASAP7_75t_R $T=9504 19440 1 0 $X=9504 $Y=18272
X3123 VSS VDD DECAPx1_ASAP7_75t_R $T=9504 38880 1 0 $X=9504 $Y=37712
X3124 VSS VDD DECAPx1_ASAP7_75t_R $T=14040 47520 0 0 $X=14040 $Y=47432
X3125 VSS VDD DECAPx1_ASAP7_75t_R $T=16632 41040 1 0 $X=16632 $Y=39872
X3126 VSS VDD DECAPx1_ASAP7_75t_R $T=17064 19440 0 0 $X=17064 $Y=19352
X3127 VSS VDD DECAPx1_ASAP7_75t_R $T=19224 19440 0 0 $X=19224 $Y=19352
X3128 VSS VDD DECAPx1_ASAP7_75t_R $T=20736 23760 0 0 $X=20736 $Y=23672
X3129 VSS VDD DECAPx1_ASAP7_75t_R $T=20736 25920 1 0 $X=20736 $Y=24752
X3130 VSS VDD DECAPx1_ASAP7_75t_R $T=22032 56160 1 0 $X=22032 $Y=54992
X3131 VSS VDD DECAPx1_ASAP7_75t_R $T=22464 6480 1 0 $X=22464 $Y=5312
X3132 VSS VDD DECAPx1_ASAP7_75t_R $T=22680 12960 1 0 $X=22680 $Y=11792
X3133 VSS VDD DECAPx1_ASAP7_75t_R $T=23112 51840 1 0 $X=23112 $Y=50672
X3134 VSS VDD DECAPx1_ASAP7_75t_R $T=24408 54000 0 0 $X=24408 $Y=53912
X3135 VSS VDD DECAPx1_ASAP7_75t_R $T=26784 49680 1 0 $X=26784 $Y=48512
X3136 VSS VDD DECAPx1_ASAP7_75t_R $T=31536 8640 1 0 $X=31536 $Y=7472
X3137 VSS VDD DECAPx1_ASAP7_75t_R $T=32616 41040 1 0 $X=32616 $Y=39872
X3138 VSS VDD DECAPx1_ASAP7_75t_R $T=35208 49680 0 0 $X=35208 $Y=49592
X3139 VSS VDD DECAPx1_ASAP7_75t_R $T=36720 43200 1 0 $X=36720 $Y=42032
X3140 VSS VDD DECAPx1_ASAP7_75t_R $T=40824 8640 1 0 $X=40824 $Y=7472
X3141 VSS VDD DECAPx1_ASAP7_75t_R $T=42552 23760 1 0 $X=42552 $Y=22592
X3142 VSS VDD DECAPx1_ASAP7_75t_R $T=43848 56160 1 0 $X=43848 $Y=54992
X3143 VSS VDD DECAPx1_ASAP7_75t_R $T=46224 34560 0 0 $X=46224 $Y=34472
X3144 VSS VDD DECAPx1_ASAP7_75t_R $T=46224 38880 0 0 $X=46224 $Y=38792
X3145 VSS VDD DECAPx1_ASAP7_75t_R $T=46440 49680 1 0 $X=46440 $Y=48512
X3146 VSS VDD DECAPx1_ASAP7_75t_R $T=47304 47520 1 0 $X=47304 $Y=46352
X3147 VSS VDD DECAPx1_ASAP7_75t_R $T=47520 25920 1 0 $X=47520 $Y=24752
X3148 VSS VDD DECAPx1_ASAP7_75t_R $T=50544 23760 0 0 $X=50544 $Y=23672
X3149 VSS VDD DECAPx1_ASAP7_75t_R $T=53136 23760 0 0 $X=53136 $Y=23672
X3150 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 12960 1 0 $X=59184 $Y=11792
X3151 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 34560 0 0 $X=59184 $Y=34472
X3152 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 54000 0 0 $X=59184 $Y=53912
X3153 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 58320 1 0 $X=59184 $Y=57152
X3154 VSS VDD ICV_6 $T=4752 43200 0 0 $X=4752 $Y=43112
X3155 VSS VDD ICV_6 $T=7776 38880 0 0 $X=7776 $Y=38792
X3156 VSS VDD ICV_6 $T=11448 28080 1 0 $X=11448 $Y=26912
X3157 VSS VDD ICV_6 $T=14256 49680 1 0 $X=14256 $Y=48512
X3158 VSS VDD ICV_6 $T=17280 43200 0 0 $X=17280 $Y=43112
X3159 VSS VDD ICV_6 $T=17280 45360 1 0 $X=17280 $Y=44192
X3160 VSS VDD ICV_6 $T=17280 49680 0 0 $X=17280 $Y=49592
X3161 VSS VDD ICV_6 $T=17712 19440 1 0 $X=17712 $Y=18272
X3162 VSS VDD ICV_6 $T=18360 12960 0 0 $X=18360 $Y=12872
X3163 VSS VDD ICV_6 $T=19008 6480 1 0 $X=19008 $Y=5312
X3164 VSS VDD ICV_6 $T=21816 8640 0 0 $X=21816 $Y=8552
X3165 VSS VDD ICV_6 $T=22032 54000 0 0 $X=22032 $Y=53912
X3166 VSS VDD ICV_6 $T=24192 8640 0 0 $X=24192 $Y=8552
X3167 VSS VDD ICV_6 $T=24840 12960 1 0 $X=24840 $Y=11792
X3168 VSS VDD ICV_6 $T=28728 23760 1 0 $X=28728 $Y=22592
X3169 VSS VDD ICV_6 $T=30456 25920 1 0 $X=30456 $Y=24752
X3170 VSS VDD ICV_6 $T=30456 58320 1 0 $X=30456 $Y=57152
X3171 VSS VDD ICV_6 $T=30672 28080 0 0 $X=30672 $Y=27992
X3172 VSS VDD ICV_6 $T=31320 10800 1 0 $X=31320 $Y=9632
X3173 VSS VDD ICV_6 $T=31752 30240 0 0 $X=31752 $Y=30152
X3174 VSS VDD ICV_6 $T=32184 32400 1 0 $X=32184 $Y=31232
X3175 VSS VDD ICV_6 $T=35424 43200 0 0 $X=35424 $Y=43112
X3176 VSS VDD ICV_6 $T=43200 34560 0 0 $X=43200 $Y=34472
X3177 VSS VDD ICV_6 $T=45792 21600 1 0 $X=45792 $Y=20432
X3178 VSS VDD ICV_6 $T=45792 54000 0 0 $X=45792 $Y=53912
X3179 VSS VDD ICV_6 $T=46656 49680 0 0 $X=46656 $Y=49592
X3180 VSS VDD ICV_6 $T=47088 43200 1 0 $X=47088 $Y=42032
X3181 VSS VDD ICV_6 $T=49464 43200 1 0 $X=49464 $Y=42032
X3182 VSS VDD ICV_6 $T=50112 19440 0 0 $X=50112 $Y=19352
X3183 VSS VDD ICV_6 $T=50760 17280 1 0 $X=50760 $Y=16112
X3184 VSS VDD ICV_6 $T=58968 10800 0 0 $X=58968 $Y=10712
X3185 VSS VDD ICV_6 $T=58968 15120 1 0 $X=58968 $Y=13952
X3186 VSS VDD ICV_6 $T=58968 32400 1 0 $X=58968 $Y=31232
X3187 VSS VDD ICV_6 $T=58968 32400 0 0 $X=58968 $Y=32312
X3188 VSS VDD ICV_6 $T=58968 49680 0 0 $X=58968 $Y=49592
X3189 VSS VDD prescale[3] 473 BUFx2_ASAP7_75t_R $T=4752 43200 1 0 $X=4752 $Y=42032
X3190 VSS VDD prescale[5] 119 BUFx2_ASAP7_75t_R $T=5184 45360 1 0 $X=5184 $Y=44192
X3191 VSS VDD 69 tx_busy BUFx2_ASAP7_75t_R $T=5400 21600 0 0 $X=5400 $Y=21512
X3192 VSS VDD prescale[4] 75 BUFx2_ASAP7_75t_R $T=5832 43200 0 0 $X=5832 $Y=43112
X3193 VSS VDD prescale[2] 583 BUFx2_ASAP7_75t_R $T=6048 41040 0 0 $X=6048 $Y=40952
X3194 VSS VDD prescale[0] 77 BUFx2_ASAP7_75t_R $T=6048 43200 1 0 $X=6048 $Y=42032
X3195 VSS VDD rst 21 BUFx2_ASAP7_75t_R $T=7128 25920 0 0 $X=7128 $Y=25832
X3196 VSS VDD prescale[1] 84 BUFx2_ASAP7_75t_R $T=7128 43200 1 0 $X=7128 $Y=42032
X3197 VSS VDD s_axis_tvalid 135 BUFx2_ASAP7_75t_R $T=15984 6480 0 0 $X=15984 $Y=6392
X3198 VSS VDD 136 s_axis_tready BUFx2_ASAP7_75t_R $T=16200 10800 0 0 $X=16200 $Y=10712
X3199 VSS VDD s_axis_tdata[7] 586 BUFx2_ASAP7_75t_R $T=18792 4320 0 0 $X=18792 $Y=4232
X3200 VSS VDD s_axis_tdata[6] 489 BUFx2_ASAP7_75t_R $T=21384 6480 1 0 $X=21384 $Y=5312
X3201 VSS VDD s_axis_tdata[5] 587 BUFx2_ASAP7_75t_R $T=22896 4320 0 0 $X=22896 $Y=4232
X3202 VSS VDD prescale[7] 207 BUFx2_ASAP7_75t_R $T=23760 58320 1 0 $X=23760 $Y=57152
X3203 VSS VDD prescale[11] 589 BUFx2_ASAP7_75t_R $T=25056 60480 1 0 $X=25056 $Y=59312
X3204 VSS VDD prescale[10] 183 BUFx2_ASAP7_75t_R $T=25488 58320 1 0 $X=25488 $Y=57152
X3205 VSS VDD s_axis_tdata[4] 506 BUFx2_ASAP7_75t_R $T=26568 4320 0 0 $X=26568 $Y=4232
X3206 VSS VDD 590 txd BUFx2_ASAP7_75t_R $T=28080 8640 0 0 $X=28080 $Y=8552
X3207 VSS VDD prescale[6] 252 BUFx2_ASAP7_75t_R $T=28944 60480 1 0 $X=28944 $Y=59312
X3208 VSS VDD prescale[9] 515 BUFx2_ASAP7_75t_R $T=29160 56160 1 0 $X=29160 $Y=54992
X3209 VSS VDD prescale[8] 178 BUFx2_ASAP7_75t_R $T=29376 58320 1 0 $X=29376 $Y=57152
X3210 VSS VDD s_axis_tdata[0] 516 BUFx2_ASAP7_75t_R $T=30024 4320 0 0 $X=30024 $Y=4232
X3211 VSS VDD s_axis_tdata[3] 519 BUFx2_ASAP7_75t_R $T=30888 8640 0 0 $X=30888 $Y=8552
X3212 VSS VDD prescale[12] 225 BUFx2_ASAP7_75t_R $T=31320 56160 0 0 $X=31320 $Y=56072
X3213 VSS VDD prescale[15] 227 BUFx2_ASAP7_75t_R $T=31536 58320 1 0 $X=31536 $Y=57152
X3214 VSS VDD prescale[13] 246 BUFx2_ASAP7_75t_R $T=31536 58320 0 0 $X=31536 $Y=58232
X3215 VSS VDD s_axis_tdata[1] 595 BUFx2_ASAP7_75t_R $T=31752 4320 0 0 $X=31752 $Y=4232
X3216 VSS VDD s_axis_tdata[2] 282 BUFx2_ASAP7_75t_R $T=32400 8640 1 0 $X=32400 $Y=7472
X3217 VSS VDD rxd 284 BUFx2_ASAP7_75t_R $T=32400 17280 1 0 $X=32400 $Y=16112
X3218 VSS VDD prescale[14] 34 BUFx2_ASAP7_75t_R $T=33048 58320 0 0 $X=33048 $Y=58232
X3219 VSS VDD 536 m_axis_tdata[6] BUFx2_ASAP7_75t_R $T=41688 8640 1 0 $X=41688 $Y=7472
X3220 VSS VDD 545 m_axis_tdata[5] BUFx2_ASAP7_75t_R $T=46440 6480 1 0 $X=46440 $Y=5312
X3221 VSS VDD 398 rx_busy BUFx2_ASAP7_75t_R $T=52488 25920 0 0 $X=52488 $Y=25832
X3222 VSS VDD 454 m_axis_tdata[4] BUFx2_ASAP7_75t_R $T=56160 12960 1 0 $X=56160 $Y=11792
X3223 VSS VDD m_axis_tready 556 BUFx2_ASAP7_75t_R $T=56160 30240 0 0 $X=56160 $Y=30152
X3224 VSS VDD 461 rx_overrun_error BUFx2_ASAP7_75t_R $T=56808 30240 1 0 $X=56808 $Y=29072
X3225 VSS VDD 463 m_axis_tdata[7] BUFx2_ASAP7_75t_R $T=57240 21600 0 0 $X=57240 $Y=21512
X3226 VSS VDD 455 m_axis_tdata[2] BUFx2_ASAP7_75t_R $T=57456 23760 0 0 $X=57456 $Y=23672
X3227 VSS VDD 459 m_axis_tdata[1] BUFx2_ASAP7_75t_R $T=57672 17280 0 0 $X=57672 $Y=17192
X3228 VSS VDD 465 m_axis_tdata[0] BUFx2_ASAP7_75t_R $T=58104 12960 1 0 $X=58104 $Y=11792
X3229 VSS VDD 470 m_axis_tdata[3] BUFx2_ASAP7_75t_R $T=58536 23760 0 0 $X=58536 $Y=23672
X3230 VSS VDD 467 m_axis_tvalid BUFx2_ASAP7_75t_R $T=58536 30240 0 0 $X=58536 $Y=30152
X3231 VSS VDD 460 rx_frame_error BUFx2_ASAP7_75t_R $T=58968 28080 0 0 $X=58968 $Y=27992
X3232 VSS VDD DECAPx2_ASAP7_75t_R $T=4752 41040 0 0 $X=4752 $Y=40952
X3233 VSS VDD DECAPx2_ASAP7_75t_R $T=8856 41040 1 0 $X=8856 $Y=39872
X3234 VSS VDD DECAPx2_ASAP7_75t_R $T=10584 30240 1 0 $X=10584 $Y=29072
X3235 VSS VDD DECAPx2_ASAP7_75t_R $T=10800 25920 0 0 $X=10800 $Y=25832
X3236 VSS VDD DECAPx2_ASAP7_75t_R $T=12528 15120 0 0 $X=12528 $Y=15032
X3237 VSS VDD DECAPx2_ASAP7_75t_R $T=12960 23760 1 0 $X=12960 $Y=22592
X3238 VSS VDD DECAPx2_ASAP7_75t_R $T=13824 41040 1 0 $X=13824 $Y=39872
X3239 VSS VDD DECAPx2_ASAP7_75t_R $T=14472 43200 1 0 $X=14472 $Y=42032
X3240 VSS VDD DECAPx2_ASAP7_75t_R $T=17280 51840 1 0 $X=17280 $Y=50672
X3241 VSS VDD DECAPx2_ASAP7_75t_R $T=17496 47520 0 0 $X=17496 $Y=47432
X3242 VSS VDD DECAPx2_ASAP7_75t_R $T=18792 25920 0 0 $X=18792 $Y=25832
X3243 VSS VDD DECAPx2_ASAP7_75t_R $T=19656 45360 1 0 $X=19656 $Y=44192
X3244 VSS VDD DECAPx2_ASAP7_75t_R $T=20952 10800 1 0 $X=20952 $Y=9632
X3245 VSS VDD DECAPx2_ASAP7_75t_R $T=23760 60480 1 0 $X=23760 $Y=59312
X3246 VSS VDD DECAPx2_ASAP7_75t_R $T=25056 47520 1 0 $X=25056 $Y=46352
X3247 VSS VDD DECAPx2_ASAP7_75t_R $T=26352 10800 0 0 $X=26352 $Y=10712
X3248 VSS VDD DECAPx2_ASAP7_75t_R $T=29160 45360 0 0 $X=29160 $Y=45272
X3249 VSS VDD DECAPx2_ASAP7_75t_R $T=30024 56160 0 0 $X=30024 $Y=56072
X3250 VSS VDD DECAPx2_ASAP7_75t_R $T=35640 28080 0 0 $X=35640 $Y=27992
X3251 VSS VDD DECAPx2_ASAP7_75t_R $T=37584 45360 0 0 $X=37584 $Y=45272
X3252 VSS VDD DECAPx2_ASAP7_75t_R $T=38448 58320 1 0 $X=38448 $Y=57152
X3253 VSS VDD DECAPx2_ASAP7_75t_R $T=38664 19440 0 0 $X=38664 $Y=19352
X3254 VSS VDD DECAPx2_ASAP7_75t_R $T=49680 51840 1 0 $X=49680 $Y=50672
X3255 VSS VDD DECAPx2_ASAP7_75t_R $T=56160 23760 0 0 $X=56160 $Y=23672
X3256 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 6480 0 0 $X=58752 $Y=6392
X3257 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 15120 0 0 $X=58752 $Y=15032
X3258 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 17280 0 0 $X=58752 $Y=17192
X3259 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 36720 1 0 $X=58752 $Y=35552
X3260 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 36720 0 0 $X=58752 $Y=36632
X3261 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 47520 0 0 $X=58752 $Y=47432
X3262 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 49680 1 0 $X=58752 $Y=48512
X3272 VSS VDD ICV_8 $T=4752 32400 0 0 $X=4752 $Y=32312
X3273 VSS VDD ICV_8 $T=7776 25920 1 0 $X=7776 $Y=24752
X3274 VSS VDD ICV_8 $T=12528 12960 0 0 $X=12528 $Y=12872
X3275 VSS VDD ICV_8 $T=14472 25920 0 0 $X=14472 $Y=25832
X3276 VSS VDD ICV_8 $T=14688 38880 1 0 $X=14688 $Y=37712
X3277 VSS VDD ICV_8 $T=17280 4320 0 0 $X=17280 $Y=4232
X3278 VSS VDD ICV_8 $T=19008 30240 0 0 $X=19008 $Y=30152
X3279 VSS VDD ICV_8 $T=19440 21600 1 0 $X=19440 $Y=20432
X3280 VSS VDD ICV_8 $T=22032 51840 0 0 $X=22032 $Y=51752
X3281 VSS VDD ICV_8 $T=25056 51840 0 0 $X=25056 $Y=51752
X3282 VSS VDD ICV_8 $T=26136 54000 1 0 $X=26136 $Y=52832
X3283 VSS VDD ICV_8 $T=31536 54000 1 0 $X=31536 $Y=52832
X3284 VSS VDD ICV_8 $T=32400 56160 0 0 $X=32400 $Y=56072
X3285 VSS VDD ICV_8 $T=35856 47520 1 0 $X=35856 $Y=46352
X3286 VSS VDD ICV_8 $T=37152 51840 1 0 $X=37152 $Y=50672
X3287 VSS VDD ICV_8 $T=37800 10800 0 0 $X=37800 $Y=10712
X3288 VSS VDD ICV_8 $T=39744 12960 1 0 $X=39744 $Y=11792
X3289 VSS VDD ICV_8 $T=40176 8640 0 0 $X=40176 $Y=8552
X3290 VSS VDD ICV_8 $T=40392 41040 1 0 $X=40392 $Y=39872
X3291 VSS VDD ICV_8 $T=42336 41040 0 0 $X=42336 $Y=40952
X3292 VSS VDD ICV_8 $T=42768 10800 1 0 $X=42768 $Y=9632
X3293 VSS VDD ICV_8 $T=42984 12960 1 0 $X=42984 $Y=11792
X3294 VSS VDD ICV_8 $T=44928 6480 1 0 $X=44928 $Y=5312
X3295 VSS VDD ICV_8 $T=45576 54000 1 0 $X=45576 $Y=52832
X3296 VSS VDD ICV_8 $T=46008 56160 1 0 $X=46008 $Y=54992
X3297 VSS VDD ICV_8 $T=47304 12960 0 0 $X=47304 $Y=12872
X3298 VSS VDD ICV_8 $T=47952 15120 0 0 $X=47952 $Y=15032
X3299 VSS VDD ICV_8 $T=50544 21600 0 0 $X=50544 $Y=21512
X3300 VSS VDD ICV_8 $T=58536 45360 0 0 $X=58536 $Y=45272
X3301 VSS VDD ICV_8 $T=58536 60480 1 0 $X=58536 $Y=59312
X3302 VSS VDD ICV_9 $T=4752 36720 1 0 $X=4752 $Y=35552
X3303 VSS VDD ICV_9 $T=6480 21600 0 0 $X=6480 $Y=21512
X3304 VSS VDD ICV_9 $T=14256 6480 0 0 $X=14256 $Y=6392
X3305 VSS VDD ICV_9 $T=14256 12960 1 0 $X=14256 $Y=11792
X3306 VSS VDD ICV_9 $T=18360 17280 0 0 $X=18360 $Y=17192
X3307 VSS VDD ICV_9 $T=29160 8640 0 0 $X=29160 $Y=8552
X3308 VSS VDD ICV_9 $T=30888 25920 0 0 $X=30888 $Y=25832
X3309 VSS VDD ICV_9 $T=34128 30240 0 0 $X=34128 $Y=30152
X3310 VSS VDD ICV_9 $T=34560 28080 1 0 $X=34560 $Y=26912
X3311 VSS VDD ICV_9 $T=35856 56160 1 0 $X=35856 $Y=54992
X3312 VSS VDD ICV_9 $T=38448 19440 1 0 $X=38448 $Y=18272
X3313 VSS VDD ICV_9 $T=39960 17280 0 0 $X=39960 $Y=17192
X3314 VSS VDD ICV_9 $T=44280 36720 1 0 $X=44280 $Y=35552
X3315 VSS VDD ICV_9 $T=47088 21600 0 0 $X=47088 $Y=21512
X3316 VSS VDD ICV_9 $T=58320 21600 0 0 $X=58320 $Y=21512
X3317 VSS VDD ICV_9 $T=58320 23760 1 0 $X=58320 $Y=22592
X3318 VSS VDD ICV_9 $T=58320 25920 0 0 $X=58320 $Y=25832
X3319 VSS VDD ICV_10 $T=8208 25920 0 0 $X=8208 $Y=25832
X3320 VSS VDD ICV_10 $T=8208 36720 0 0 $X=8208 $Y=36632
X3321 VSS VDD ICV_10 $T=12312 36720 0 0 $X=12312 $Y=36632
X3322 VSS VDD ICV_10 $T=14256 10800 0 0 $X=14256 $Y=10712
X3323 VSS VDD ICV_10 $T=18792 47520 1 0 $X=18792 $Y=46352
X3324 VSS VDD ICV_10 $T=19224 36720 0 0 $X=19224 $Y=36632
X3325 VSS VDD ICV_10 $T=19656 17280 1 0 $X=19656 $Y=16112
X3326 VSS VDD ICV_10 $T=24408 34560 1 0 $X=24408 $Y=33392
X3327 VSS VDD ICV_10 $T=31968 8640 0 0 $X=31968 $Y=8552
X3328 VSS VDD ICV_10 $T=36072 56160 0 0 $X=36072 $Y=56072
X3329 VSS VDD ICV_10 $T=39096 15120 1 0 $X=39096 $Y=13952
X3330 VSS VDD ICV_10 $T=40392 49680 0 0 $X=40392 $Y=49592
X3331 VSS VDD ICV_10 $T=40824 47520 0 0 $X=40824 $Y=47432
X3332 VSS VDD ICV_10 $T=41472 23760 0 0 $X=41472 $Y=23672
X3333 VSS VDD ICV_10 $T=42552 15120 1 0 $X=42552 $Y=13952
X3334 VSS VDD ICV_10 $T=42984 43200 0 0 $X=42984 $Y=43112
X3335 VSS VDD ICV_10 $T=43632 21600 0 0 $X=43632 $Y=21512
X3336 VSS VDD ICV_10 $T=46224 12960 1 0 $X=46224 $Y=11792
X3337 VSS VDD ICV_10 $T=49896 25920 1 0 $X=49896 $Y=24752
X3338 VSS VDD ICV_10 $T=54432 17280 0 0 $X=54432 $Y=17192
X3339 VSS VDD ICV_10 $T=57024 28080 0 0 $X=57024 $Y=27992
X3340 VSS VDD ICV_10 $T=58104 10800 1 0 $X=58104 $Y=9632
X3341 VSS VDD ICV_10 $T=58104 56160 0 0 $X=58104 $Y=56072
X3342 VSS VDD DECAPx4_ASAP7_75t_R $T=4752 34560 1 0 $X=4752 $Y=33392
X3343 VSS VDD DECAPx4_ASAP7_75t_R $T=4752 36720 0 0 $X=4752 $Y=36632
X3344 VSS VDD DECAPx4_ASAP7_75t_R $T=9936 47520 1 0 $X=9936 $Y=46352
X3345 VSS VDD DECAPx4_ASAP7_75t_R $T=12312 36720 1 0 $X=12312 $Y=35552
X3346 VSS VDD DECAPx4_ASAP7_75t_R $T=16848 28080 1 0 $X=16848 $Y=26912
X3347 VSS VDD DECAPx4_ASAP7_75t_R $T=21816 28080 1 0 $X=21816 $Y=26912
X3348 VSS VDD DECAPx4_ASAP7_75t_R $T=23760 34560 0 0 $X=23760 $Y=34472
X3349 VSS VDD DECAPx4_ASAP7_75t_R $T=24624 6480 1 0 $X=24624 $Y=5312
X3350 VSS VDD DECAPx4_ASAP7_75t_R $T=25056 36720 1 0 $X=25056 $Y=35552
X3351 VSS VDD DECAPx4_ASAP7_75t_R $T=25488 6480 0 0 $X=25488 $Y=6392
X3352 VSS VDD DECAPx4_ASAP7_75t_R $T=37152 47520 0 0 $X=37152 $Y=47432
X3353 VSS VDD DECAPx4_ASAP7_75t_R $T=40608 51840 0 0 $X=40608 $Y=51752
X3354 VSS VDD DECAPx4_ASAP7_75t_R $T=43416 17280 0 0 $X=43416 $Y=17192
X3355 VSS VDD DECAPx4_ASAP7_75t_R $T=50328 25920 0 0 $X=50328 $Y=25832
X3356 VSS VDD DECAPx4_ASAP7_75t_R $T=57888 30240 1 0 $X=57888 $Y=29072
X3357 VSS VDD DECAPx4_ASAP7_75t_R $T=57888 51840 0 0 $X=57888 $Y=51752
X3382 VSS VDD ICV_12 $T=4752 25920 0 0 $X=4752 $Y=25832
X3383 VSS VDD ICV_12 $T=4752 45360 0 0 $X=4752 $Y=45272
X3384 VSS VDD ICV_12 $T=13176 43200 0 0 $X=13176 $Y=43112
X3385 VSS VDD ICV_12 $T=14256 21600 1 0 $X=14256 $Y=20432
X3386 VSS VDD ICV_12 $T=23112 49680 1 0 $X=23112 $Y=48512
X3387 VSS VDD ICV_12 $T=23976 41040 0 0 $X=23976 $Y=40952
X3388 VSS VDD ICV_12 $T=25704 32400 0 0 $X=25704 $Y=32312
X3389 VSS VDD ICV_12 $T=27648 4320 0 0 $X=27648 $Y=4232
X3390 VSS VDD ICV_12 $T=40392 34560 1 0 $X=40392 $Y=33392
X3391 VSS VDD ICV_12 $T=44064 17280 1 0 $X=44064 $Y=16112
X3392 VSS VDD ICV_12 $T=57672 34560 1 0 $X=57672 $Y=33392
X3393 VSS VDD 606 69 INVx1_ASAP7_75t_R $T=8208 21600 0 0 $X=8208 $Y=21512
X3394 VSS VDD 84 87 INVx1_ASAP7_75t_R $T=8208 41040 1 0 $X=8208 $Y=39872
X3395 VSS VDD 77 126 INVx1_ASAP7_75t_R $T=9504 41040 0 0 $X=9504 $Y=40952
X3396 VSS VDD 90 484 INVx1_ASAP7_75t_R $T=10152 25920 0 0 $X=10152 $Y=25832
X3397 VSS VDD 266 477 INVx1_ASAP7_75t_R $T=10152 41040 1 0 $X=10152 $Y=39872
X3398 VSS VDD 481 607 INVx1_ASAP7_75t_R $T=13176 30240 0 0 $X=13176 $Y=30152
X3399 VSS VDD 120 608 INVx1_ASAP7_75t_R $T=13824 15120 0 0 $X=13824 $Y=15032
X3400 VSS VDD 128 136 INVx1_ASAP7_75t_R $T=15984 12960 1 0 $X=15984 $Y=11792
X3401 VSS VDD 23 609 INVx1_ASAP7_75t_R $T=18144 34560 0 0 $X=18144 $Y=34472
X3402 VSS VDD 158 127 INVx1_ASAP7_75t_R $T=18360 23760 1 0 $X=18360 $Y=22592
X3403 VSS VDD 29 610 INVx1_ASAP7_75t_R $T=18576 36720 0 0 $X=18576 $Y=36632
X3404 VSS VDD 135 611 INVx1_ASAP7_75t_R $T=18792 19440 1 0 $X=18792 $Y=18272
X3405 VSS VDD 5 612 INVx1_ASAP7_75t_R $T=19008 41040 0 0 $X=19008 $Y=40952
X3406 VSS VDD 9 198 INVx1_ASAP7_75t_R $T=22248 28080 0 0 $X=22248 $Y=27992
X3407 VSS VDD 186 613 INVx1_ASAP7_75t_R $T=22464 21600 0 0 $X=22464 $Y=21512
X3408 VSS VDD 569 192 INVx1_ASAP7_75t_R $T=22896 15120 0 0 $X=22896 $Y=15032
X3409 VSS VDD 12 494 INVx1_ASAP7_75t_R $T=23328 23760 1 0 $X=23328 $Y=22592
X3410 VSS VDD 497 165 INVx1_ASAP7_75t_R $T=24408 17280 1 0 $X=24408 $Y=16112
X3411 VSS VDD 500 614 INVx1_ASAP7_75t_R $T=25056 17280 1 0 $X=25056 $Y=16112
X3412 VSS VDD 229 507 INVx1_ASAP7_75t_R $T=27000 32400 1 0 $X=27000 $Y=31232
X3413 VSS VDD 228 221 INVx1_ASAP7_75t_R $T=27216 12960 0 0 $X=27216 $Y=12872
X3414 VSS VDD 232 590 INVx1_ASAP7_75t_R $T=27648 10800 0 0 $X=27648 $Y=10712
X3415 VSS VDD 510 261 INVx1_ASAP7_75t_R $T=30240 34560 0 0 $X=30240 $Y=34472
X3416 VSS VDD 280 234 INVx1_ASAP7_75t_R $T=30240 41040 1 0 $X=30240 $Y=39872
X3417 VSS VDD 285 265 INVx1_ASAP7_75t_R $T=34560 25920 1 0 $X=34560 $Y=24752
X3418 VSS VDD 313 275 INVx1_ASAP7_75t_R $T=34992 28080 0 0 $X=34992 $Y=27992
X3419 VSS VDD 300 327 INVx1_ASAP7_75t_R $T=35856 30240 0 0 $X=35856 $Y=30152
X3420 VSS VDD 530 343 INVx1_ASAP7_75t_R $T=36504 30240 0 0 $X=36504 $Y=30152
X3421 VSS VDD 321 303 INVx1_ASAP7_75t_R $T=37368 32400 1 0 $X=37368 $Y=31232
X3422 VSS VDD 323 326 INVx1_ASAP7_75t_R $T=37368 47520 1 0 $X=37368 $Y=46352
X3423 VSS VDD 355 536 INVx1_ASAP7_75t_R $T=41688 8640 0 0 $X=41688 $Y=8552
X3424 VSS VDD 367 345 INVx1_ASAP7_75t_R $T=43200 19440 0 0 $X=43200 $Y=19352
X3425 VSS VDD 574 356 INVx1_ASAP7_75t_R $T=43416 17280 1 0 $X=43416 $Y=16112
X3426 VSS VDD 383 385 INVx1_ASAP7_75t_R $T=44496 38880 1 0 $X=44496 $Y=37712
X3427 VSS VDD 373 380 INVx1_ASAP7_75t_R $T=45792 41040 1 0 $X=45792 $Y=39872
X3428 VSS VDD 394 545 INVx1_ASAP7_75t_R $T=46224 8640 1 0 $X=46224 $Y=7472
X3429 VSS VDD 396 382 INVx1_ASAP7_75t_R $T=46656 12960 0 0 $X=46656 $Y=12872
X3430 VSS VDD 402 550 INVx1_ASAP7_75t_R $T=47952 41040 1 0 $X=47952 $Y=39872
X3431 VSS VDD 413 615 INVx1_ASAP7_75t_R $T=48384 30240 0 0 $X=48384 $Y=30152
X3432 VSS VDD 414 420 INVx1_ASAP7_75t_R $T=48600 41040 0 0 $X=48600 $Y=40952
X3433 VSS VDD 418 398 INVx1_ASAP7_75t_R $T=49680 25920 0 0 $X=49680 $Y=25832
X3434 VSS VDD 427 412 INVx1_ASAP7_75t_R $T=49896 23760 1 0 $X=49896 $Y=22592
X3435 VSS VDD 425 389 INVx1_ASAP7_75t_R $T=49896 23760 0 0 $X=49896 $Y=23672
X3436 VSS VDD 444 423 INVx1_ASAP7_75t_R $T=53568 15120 1 0 $X=53568 $Y=13952
X3437 VSS VDD 451 461 INVx1_ASAP7_75t_R $T=55512 30240 0 0 $X=55512 $Y=30152
X3438 VSS VDD 442 459 INVx1_ASAP7_75t_R $T=56376 17280 0 0 $X=56376 $Y=17192
X3439 VSS VDD 457 460 INVx1_ASAP7_75t_R $T=56376 28080 0 0 $X=56376 $Y=27992
X3440 VSS VDD 431 463 INVx1_ASAP7_75t_R $T=56592 21600 1 0 $X=56592 $Y=20432
X3441 VSS VDD 447 465 INVx1_ASAP7_75t_R $T=57240 12960 1 0 $X=57240 $Y=11792
X3442 VSS VDD 437 470 INVx1_ASAP7_75t_R $T=57672 23760 1 0 $X=57672 $Y=22592
X3452 VSS VDD ICV_14 $T=10584 30240 0 0 $X=10584 $Y=30152
X3453 VSS VDD ICV_14 $T=14256 8640 1 0 $X=14256 $Y=7472
X3454 VSS VDD ICV_14 $T=15336 34560 1 0 $X=15336 $Y=33392
X3455 VSS VDD ICV_14 $T=16848 49680 1 0 $X=16848 $Y=48512
X3456 VSS VDD ICV_14 $T=17064 6480 0 0 $X=17064 $Y=6392
X3457 VSS VDD ICV_14 $T=17928 45360 0 0 $X=17928 $Y=45272
X3458 VSS VDD ICV_14 $T=23976 4320 0 0 $X=23976 $Y=4232
X3459 VSS VDD ICV_14 $T=24408 38880 0 0 $X=24408 $Y=38792
X3460 VSS VDD ICV_14 $T=26568 56160 1 0 $X=26568 $Y=54992
X3461 VSS VDD ICV_14 $T=27864 12960 0 0 $X=27864 $Y=12872
X3462 VSS VDD ICV_14 $T=28296 30240 1 0 $X=28296 $Y=29072
X3463 VSS VDD ICV_14 $T=29376 21600 0 0 $X=29376 $Y=21512
X3464 VSS VDD ICV_14 $T=30888 54000 0 0 $X=30888 $Y=53912
X3465 VSS VDD ICV_14 $T=31536 19440 1 0 $X=31536 $Y=18272
X3466 VSS VDD ICV_14 $T=38232 36720 0 0 $X=38232 $Y=36632
X3467 VSS VDD ICV_14 $T=40392 54000 0 0 $X=40392 $Y=53912
X3468 VSS VDD ICV_14 $T=49464 28080 0 0 $X=49464 $Y=27992
X3469 VSS VDD ICV_14 $T=57456 41040 0 0 $X=57456 $Y=40952
X3470 VSS VDD 142 140 ICV_15 $T=17280 17280 1 0 $X=17280 $Y=16112
X3471 VSS VDD 143 151 ICV_15 $T=17928 25920 0 0 $X=17928 $Y=25832
X3472 VSS VDD 26 193 ICV_15 $T=21600 38880 1 0 $X=21600 $Y=37712
X3473 VSS VDD 21 44 ICV_15 $T=40392 23760 1 0 $X=40392 $Y=22592
X3474 VSS VDD 401 576 ICV_15 $T=46440 41040 1 0 $X=46440 $Y=39872
X3475 VSS VDD 424 422 ICV_15 $T=47952 17280 1 0 $X=47952 $Y=16112
X3476 VSS VDD 439 617 ICV_15 $T=49896 17280 0 0 $X=49896 $Y=17192
X3477 VSS VDD 410 454 ICV_15 $T=55296 12960 1 0 $X=55296 $Y=11792
X3478 VSS VDD 448 455 ICV_15 $T=55296 23760 0 0 $X=55296 $Y=23672
X3479 VSS VDD 464 467 ICV_15 $T=57240 30240 0 0 $X=57240 $Y=30152
X3480 VSS VDD ICV_16 $T=4752 23760 1 0 $X=4752 $Y=22592
X3481 VSS VDD ICV_16 $T=4752 30240 0 0 $X=4752 $Y=30152
X3482 VSS VDD ICV_16 $T=4752 47520 1 0 $X=4752 $Y=46352
X3483 VSS VDD ICV_16 $T=11880 21600 0 0 $X=11880 $Y=21512
X3484 VSS VDD ICV_16 $T=15552 23760 1 0 $X=15552 $Y=22592
X3485 VSS VDD ICV_16 $T=26136 60480 1 0 $X=26136 $Y=59312
X3486 VSS VDD ICV_16 $T=26568 58320 1 0 $X=26568 $Y=57152
X3487 VSS VDD ICV_16 $T=28080 6480 1 0 $X=28080 $Y=5312
X3488 VSS VDD ICV_16 $T=30456 45360 1 0 $X=30456 $Y=44192
X3489 VSS VDD ICV_16 $T=34560 32400 1 0 $X=34560 $Y=31232
X3490 VSS VDD ICV_16 $T=35424 23760 1 0 $X=35424 $Y=22592
X3491 VSS VDD ICV_16 $T=36072 51840 0 0 $X=36072 $Y=51752
X3492 VSS VDD ICV_16 $T=39528 12960 0 0 $X=39528 $Y=12872
X3493 VSS VDD ICV_16 $T=45792 41040 0 0 $X=45792 $Y=40952
X3494 VSS VDD ICV_16 $T=57240 12960 0 0 $X=57240 $Y=12872
X3495 VSS VDD ICV_16 $T=57240 21600 1 0 $X=57240 $Y=20432
X3644 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 25920 1 0 $X=4752 $Y=24752
X3645 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 28080 0 0 $X=4752 $Y=27992
X3646 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 30240 1 0 $X=4752 $Y=29072
X3647 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 38880 0 0 $X=4752 $Y=38792
X3648 VSS VDD DECAPx6_ASAP7_75t_R $T=10800 41040 1 0 $X=10800 $Y=39872
X3649 VSS VDD DECAPx6_ASAP7_75t_R $T=11448 38880 0 0 $X=11448 $Y=38792
X3650 VSS VDD DECAPx6_ASAP7_75t_R $T=11664 38880 1 0 $X=11664 $Y=37712
X3651 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 4320 0 0 $X=14256 $Y=4232
X3652 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 17280 1 0 $X=14256 $Y=16112
X3653 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 49680 0 0 $X=14256 $Y=49592
X3654 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 51840 1 0 $X=14256 $Y=50672
X3655 VSS VDD DECAPx6_ASAP7_75t_R $T=14688 19440 1 0 $X=14688 $Y=18272
X3656 VSS VDD DECAPx6_ASAP7_75t_R $T=15984 30240 0 0 $X=15984 $Y=30152
X3657 VSS VDD DECAPx6_ASAP7_75t_R $T=16200 23760 0 0 $X=16200 $Y=23672
X3658 VSS VDD DECAPx6_ASAP7_75t_R $T=19872 4320 0 0 $X=19872 $Y=4232
X3659 VSS VDD DECAPx6_ASAP7_75t_R $T=22032 30240 0 0 $X=22032 $Y=30152
X3660 VSS VDD DECAPx6_ASAP7_75t_R $T=22896 49680 0 0 $X=22896 $Y=49592
X3661 VSS VDD DECAPx6_ASAP7_75t_R $T=23328 45360 1 0 $X=23328 $Y=44192
X3662 VSS VDD DECAPx6_ASAP7_75t_R $T=23328 45360 0 0 $X=23328 $Y=45272
X3663 VSS VDD DECAPx6_ASAP7_75t_R $T=24624 38880 1 0 $X=24624 $Y=37712
X3664 VSS VDD DECAPx6_ASAP7_75t_R $T=26352 17280 0 0 $X=26352 $Y=17192
X3665 VSS VDD DECAPx6_ASAP7_75t_R $T=26784 15120 1 0 $X=26784 $Y=13952
X3666 VSS VDD DECAPx6_ASAP7_75t_R $T=27000 56160 0 0 $X=27000 $Y=56072
X3667 VSS VDD DECAPx6_ASAP7_75t_R $T=27648 32400 1 0 $X=27648 $Y=31232
X3668 VSS VDD DECAPx6_ASAP7_75t_R $T=30240 12960 1 0 $X=30240 $Y=11792
X3669 VSS VDD DECAPx6_ASAP7_75t_R $T=32616 58320 1 0 $X=32616 $Y=57152
X3670 VSS VDD DECAPx6_ASAP7_75t_R $T=32832 38880 1 0 $X=32832 $Y=37712
X3671 VSS VDD DECAPx6_ASAP7_75t_R $T=35208 54000 1 0 $X=35208 $Y=52832
X3672 VSS VDD DECAPx6_ASAP7_75t_R $T=36072 38880 0 0 $X=36072 $Y=38792
X3673 VSS VDD DECAPx6_ASAP7_75t_R $T=36288 21600 0 0 $X=36288 $Y=21512
X3674 VSS VDD DECAPx6_ASAP7_75t_R $T=36720 21600 1 0 $X=36720 $Y=20432
X3675 VSS VDD DECAPx6_ASAP7_75t_R $T=40392 45360 1 0 $X=40392 $Y=44192
X3676 VSS VDD DECAPx6_ASAP7_75t_R $T=40392 45360 0 0 $X=40392 $Y=45272
X3677 VSS VDD DECAPx6_ASAP7_75t_R $T=40824 28080 1 0 $X=40824 $Y=26912
X3678 VSS VDD DECAPx6_ASAP7_75t_R $T=43632 49680 0 0 $X=43632 $Y=49592
X3679 VSS VDD DECAPx6_ASAP7_75t_R $T=44496 19440 1 0 $X=44496 $Y=18272
X3680 VSS VDD DECAPx6_ASAP7_75t_R $T=46008 15120 1 0 $X=46008 $Y=13952
X3681 VSS VDD DECAPx6_ASAP7_75t_R $T=46656 51840 1 0 $X=46656 $Y=50672
X3682 VSS VDD DECAPx6_ASAP7_75t_R $T=47304 45360 1 0 $X=47304 $Y=44192
X3683 VSS VDD DECAPx6_ASAP7_75t_R $T=55512 45360 0 0 $X=55512 $Y=45272
X3684 VSS VDD DECAPx6_ASAP7_75t_R $T=55728 6480 0 0 $X=55728 $Y=6392
X3685 VSS VDD DECAPx6_ASAP7_75t_R $T=55728 49680 1 0 $X=55728 $Y=48512
X3686 VSS VDD DECAPx6_ASAP7_75t_R $T=55944 10800 0 0 $X=55944 $Y=10712
X3687 VSS VDD DECAPx6_ASAP7_75t_R $T=55944 32400 1 0 $X=55944 $Y=31232
X3688 VSS VDD DECAPx6_ASAP7_75t_R $T=56376 8640 1 0 $X=56376 $Y=7472
X3689 VSS VDD DECAPx6_ASAP7_75t_R $T=57024 6480 1 0 $X=57024 $Y=5312
X3810 VSS VDD ICV_19 $T=4752 28080 1 0 $X=4752 $Y=26912
X3811 VSS VDD ICV_19 $T=4752 41040 1 0 $X=4752 $Y=39872
X3812 VSS VDD ICV_19 $T=11448 32400 0 0 $X=11448 $Y=32312
X3813 VSS VDD ICV_19 $T=12528 41040 0 0 $X=12528 $Y=40952
X3814 VSS VDD ICV_19 $T=14256 8640 0 0 $X=14256 $Y=8552
X3815 VSS VDD ICV_19 $T=21384 25920 0 0 $X=21384 $Y=25832
X3816 VSS VDD ICV_19 $T=29592 49680 0 0 $X=29592 $Y=49592
X3817 VSS VDD ICV_19 $T=30240 56160 1 0 $X=30240 $Y=54992
X3818 VSS VDD ICV_19 $T=32184 34560 0 0 $X=32184 $Y=34472
X3819 VSS VDD ICV_19 $T=36288 15120 0 0 $X=36288 $Y=15032
X3820 VSS VDD ICV_19 $T=38016 47520 1 0 $X=38016 $Y=46352
X3821 VSS VDD ICV_19 $T=42768 8640 1 0 $X=42768 $Y=7472
X3822 VSS VDD ICV_19 $T=56592 28080 1 0 $X=56592 $Y=26912
X3823 VSS VDD ICV_19 $T=56592 51840 1 0 $X=56592 $Y=50672
X3824 VSS VDD ICV_20 $T=12960 17280 0 0 $X=12960 $Y=17192
X3825 VSS VDD ICV_20 $T=25704 17280 1 0 $X=25704 $Y=16112
X3826 VSS VDD ICV_20 $T=28296 10800 0 0 $X=28296 $Y=10712
X3827 VSS VDD ICV_20 $T=41040 25920 0 0 $X=41040 $Y=25832
X3828 VSS VDD ICV_20 $T=52920 30240 1 0 $X=52920 $Y=29072
X3829 VSS VDD ICV_20 $T=53352 21600 0 0 $X=53352 $Y=21512
X3830 VSS VDD ICV_20 $T=56160 17280 1 0 $X=56160 $Y=16112
X3831 VSS VDD ICV_20 $T=56160 25920 1 0 $X=56160 $Y=24752
X3832 VSS VDD ICV_20 $T=56160 38880 1 0 $X=56160 $Y=37712
X3833 VSS VDD ICV_20 $T=56160 43200 0 0 $X=56160 $Y=43112
X3869 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 17280 0 0 $X=4752 $Y=17192
X3870 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 19440 1 0 $X=4752 $Y=18272
X3871 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 19440 0 0 $X=4752 $Y=19352
X3872 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 38880 1 0 $X=4752 $Y=37712
X3873 VSS VDD DECAPx10_ASAP7_75t_R $T=10584 34560 1 0 $X=10584 $Y=33392
X3874 VSS VDD DECAPx10_ASAP7_75t_R $T=11232 45360 1 0 $X=11232 $Y=44192
X3875 VSS VDD DECAPx10_ASAP7_75t_R $T=11664 34560 0 0 $X=11664 $Y=34472
X3876 VSS VDD DECAPx10_ASAP7_75t_R $T=12312 19440 0 0 $X=12312 $Y=19352
X3877 VSS VDD DECAPx10_ASAP7_75t_R $T=14256 6480 1 0 $X=14256 $Y=5312
X3878 VSS VDD DECAPx10_ASAP7_75t_R $T=14256 10800 1 0 $X=14256 $Y=9632
X3879 VSS VDD DECAPx10_ASAP7_75t_R $T=17496 15120 0 0 $X=17496 $Y=15032
X3880 VSS VDD DECAPx10_ASAP7_75t_R $T=17928 12960 1 0 $X=17928 $Y=11792
X3881 VSS VDD DECAPx10_ASAP7_75t_R $T=20088 32400 1 0 $X=20088 $Y=31232
X3882 VSS VDD DECAPx10_ASAP7_75t_R $T=20088 43200 0 0 $X=20088 $Y=43112
X3883 VSS VDD DECAPx10_ASAP7_75t_R $T=21816 47520 0 0 $X=21816 $Y=47432
X3884 VSS VDD DECAPx10_ASAP7_75t_R $T=23976 23760 1 0 $X=23976 $Y=22592
X3885 VSS VDD DECAPx10_ASAP7_75t_R $T=24624 21600 0 0 $X=24624 $Y=21512
X3886 VSS VDD DECAPx10_ASAP7_75t_R $T=26136 25920 0 0 $X=26136 $Y=25832
X3887 VSS VDD DECAPx10_ASAP7_75t_R $T=26784 19440 1 0 $X=26784 $Y=18272
X3888 VSS VDD DECAPx10_ASAP7_75t_R $T=27000 30240 0 0 $X=27000 $Y=30152
X3889 VSS VDD DECAPx10_ASAP7_75t_R $T=28944 51840 0 0 $X=28944 $Y=51752
X3890 VSS VDD DECAPx10_ASAP7_75t_R $T=29808 28080 1 0 $X=29808 $Y=26912
X3891 VSS VDD DECAPx10_ASAP7_75t_R $T=30456 47520 0 0 $X=30456 $Y=47432
X3892 VSS VDD DECAPx10_ASAP7_75t_R $T=31104 47520 1 0 $X=31104 $Y=46352
X3893 VSS VDD DECAPx10_ASAP7_75t_R $T=31320 36720 0 0 $X=31320 $Y=36632
X3894 VSS VDD DECAPx10_ASAP7_75t_R $T=32832 45360 0 0 $X=32832 $Y=45272
X3895 VSS VDD DECAPx10_ASAP7_75t_R $T=34128 58320 0 0 $X=34128 $Y=58232
X3896 VSS VDD DECAPx10_ASAP7_75t_R $T=34344 15120 1 0 $X=34344 $Y=13952
X3897 VSS VDD DECAPx10_ASAP7_75t_R $T=34776 12960 0 0 $X=34776 $Y=12872
X3898 VSS VDD DECAPx10_ASAP7_75t_R $T=34992 12960 1 0 $X=34992 $Y=11792
X3899 VSS VDD DECAPx10_ASAP7_75t_R $T=35208 17280 0 0 $X=35208 $Y=17192
X3900 VSS VDD DECAPx10_ASAP7_75t_R $T=35424 8640 0 0 $X=35424 $Y=8552
X3901 VSS VDD DECAPx10_ASAP7_75t_R $T=35640 54000 0 0 $X=35640 $Y=53912
X3902 VSS VDD DECAPx10_ASAP7_75t_R $T=36072 8640 1 0 $X=36072 $Y=7472
X3903 VSS VDD DECAPx10_ASAP7_75t_R $T=36288 49680 1 0 $X=36288 $Y=48512
X3904 VSS VDD DECAPx10_ASAP7_75t_R $T=37584 41040 0 0 $X=37584 $Y=40952
X3905 VSS VDD DECAPx10_ASAP7_75t_R $T=38448 34560 0 0 $X=38448 $Y=34472
X3906 VSS VDD DECAPx10_ASAP7_75t_R $T=39312 43200 1 0 $X=39312 $Y=42032
X3907 VSS VDD DECAPx10_ASAP7_75t_R $T=41040 21600 1 0 $X=41040 $Y=20432
X3908 VSS VDD DECAPx10_ASAP7_75t_R $T=42768 25920 1 0 $X=42768 $Y=24752
X3909 VSS VDD DECAPx10_ASAP7_75t_R $T=43632 32400 0 0 $X=43632 $Y=32312
X3910 VSS VDD DECAPx10_ASAP7_75t_R $T=43848 19440 0 0 $X=43848 $Y=19352
X3911 VSS VDD DECAPx10_ASAP7_75t_R $T=52704 41040 0 0 $X=52704 $Y=40952
X3912 VSS VDD DECAPx10_ASAP7_75t_R $T=53568 25920 0 0 $X=53568 $Y=25832
X3913 VSS VDD DECAPx10_ASAP7_75t_R $T=54000 47520 0 0 $X=54000 $Y=47432
X3914 VSS VDD DECAPx10_ASAP7_75t_R $T=54216 15120 1 0 $X=54216 $Y=13952
X3915 VSS VDD DECAPx10_ASAP7_75t_R $T=54864 38880 0 0 $X=54864 $Y=38792
X4125 VSS VDD 144 138 10 NAND2x1_ASAP7_75t_R $T=14256 23760 1 0 $X=14256 $Y=22592
X4126 VSS VDD 7 6 149 NAND2x1_ASAP7_75t_R $T=15984 41040 0 0 $X=15984 $Y=40952
X4127 VSS VDD 144 94 152 NAND2x1_ASAP7_75t_R $T=17496 28080 0 0 $X=17496 $Y=27992
X4128 VSS VDD 7 624 157 NAND2x1_ASAP7_75t_R $T=17928 34560 1 0 $X=17928 $Y=33392
X4129 VSS VDD 22 17 171 NAND2x1_ASAP7_75t_R $T=19224 28080 1 0 $X=19224 $Y=26912
X4130 VSS VDD 164 173 670 NAND2x1_ASAP7_75t_R $T=19656 10800 1 0 $X=19656 $Y=9632
X4131 VSS VDD 165 498 16 NAND2x1_ASAP7_75t_R $T=20088 19440 0 0 $X=20088 $Y=19352
X4132 VSS VDD 166 151 174 NAND2x1_ASAP7_75t_R $T=20088 25920 0 0 $X=20088 $Y=25832
X4133 VSS VDD 12 243 665 NAND2x1_ASAP7_75t_R $T=21600 19440 0 0 $X=21600 $Y=19352
X4134 VSS VDD 182 173 191 NAND2x1_ASAP7_75t_R $T=22248 10800 1 0 $X=22248 $Y=9632
X4135 VSS VDD 24 133 208 NAND2x1_ASAP7_75t_R $T=23760 30240 1 0 $X=23760 $Y=29072
X4136 VSS VDD 166 502 209 NAND2x1_ASAP7_75t_R $T=24840 25920 0 0 $X=24840 $Y=25832
X4137 VSS VDD 501 173 206 NAND2x1_ASAP7_75t_R $T=25272 8640 0 0 $X=25272 $Y=8552
X4138 VSS VDD 234 512 239 NAND2x1_ASAP7_75t_R $T=28944 41040 1 0 $X=28944 $Y=39872
X4139 VSS VDD 245 173 237 NAND2x1_ASAP7_75t_R $T=29160 6480 0 0 $X=29160 $Y=6392
X4140 VSS VDD 251 173 258 NAND2x1_ASAP7_75t_R $T=29592 17280 1 0 $X=29592 $Y=16112
X4141 VSS VDD 265 314 263 NAND2x1_ASAP7_75t_R $T=31536 25920 1 0 $X=31536 $Y=24752
X4142 VSS VDD 275 293 287 NAND2x1_ASAP7_75t_R $T=32832 30240 0 0 $X=32832 $Y=30152
X4143 VSS VDD 267 173 631 NAND2x1_ASAP7_75t_R $T=33048 15120 1 0 $X=33048 $Y=13952
X4144 VSS VDD 283 173 528 NAND2x1_ASAP7_75t_R $T=33696 10800 1 0 $X=33696 $Y=9632
X4145 VSS VDD 289 173 305 NAND2x1_ASAP7_75t_R $T=34128 6480 1 0 $X=34128 $Y=5312
X4146 VSS VDD 297 314 277 NAND2x1_ASAP7_75t_R $T=34128 23760 1 0 $X=34128 $Y=22592
X4147 VSS VDD 292 301 306 NAND2x1_ASAP7_75t_R $T=34128 41040 0 0 $X=34128 $Y=40952
X4148 VSS VDD 279 530 533 NAND2x1_ASAP7_75t_R $T=36288 28080 1 0 $X=36288 $Y=26912
X4149 VSS VDD 318 292 291 NAND2x1_ASAP7_75t_R $T=37152 34560 0 0 $X=37152 $Y=34472
X4150 VSS VDD 323 599 534 NAND2x1_ASAP7_75t_R $T=37152 58320 1 0 $X=37152 $Y=57152
X4151 VSS VDD 303 337 335 NAND2x1_ASAP7_75t_R $T=38016 32400 1 0 $X=38016 $Y=31232
X4152 VSS VDD 331 313 321 NAND2x1_ASAP7_75t_R $T=38448 25920 0 0 $X=38448 $Y=25832
X4153 VSS VDD 355 434 603 NAND2x1_ASAP7_75t_R $T=43632 10800 0 0 $X=43632 $Y=10712
X4154 VSS VDD 384 554 378 NAND2x1_ASAP7_75t_R $T=44712 56160 1 0 $X=44712 $Y=54992
X4155 VSS VDD 394 434 656 NAND2x1_ASAP7_75t_R $T=45144 10800 0 0 $X=45144 $Y=10712
X4156 VSS VDD 410 434 660 NAND2x1_ASAP7_75t_R $T=48168 12960 1 0 $X=48168 $Y=11792
X4157 VSS VDD 400 393 551 NAND2x1_ASAP7_75t_R $T=48168 43200 1 0 $X=48168 $Y=42032
X4158 VSS VDD 554 426 434 NAND2x1_ASAP7_75t_R $T=49896 32400 0 0 $X=49896 $Y=32312
X4159 VSS VDD 431 434 561 NAND2x1_ASAP7_75t_R $T=50976 21600 1 0 $X=50976 $Y=20432
X4160 VSS VDD 437 434 671 NAND2x1_ASAP7_75t_R $T=52056 21600 0 0 $X=52056 $Y=21512
X4161 VSS VDD 442 434 449 NAND2x1_ASAP7_75t_R $T=53136 17280 0 0 $X=53136 $Y=17192
X4162 VSS VDD 447 434 452 NAND2x1_ASAP7_75t_R $T=54000 12960 1 0 $X=54000 $Y=11792
X4163 VSS VDD 448 434 453 NAND2x1_ASAP7_75t_R $T=54000 23760 0 0 $X=54000 $Y=23672
X4223 VSS VDD ICV_25 $T=20520 41040 1 0 $X=20520 $Y=39872
X4224 VSS VDD ICV_25 $T=23112 23760 0 0 $X=23112 $Y=23672
X4225 VSS VDD ICV_25 $T=28512 43200 1 0 $X=28512 $Y=42032
X4226 VSS VDD ICV_25 $T=52488 19440 1 0 $X=52488 $Y=18272
X4227 VSS VDD ICV_25 $T=52920 19440 0 0 $X=52920 $Y=19352
X4228 VSS VDD ICV_25 $T=52920 43200 1 0 $X=52920 $Y=42032
X4229 VSS VDD ICV_25 $T=52920 45360 1 0 $X=52920 $Y=44192
X4230 VSS VDD ICV_25 $T=52920 58320 0 0 $X=52920 $Y=58232
X4231 VSS VDD ICV_25 $T=53136 47520 1 0 $X=53136 $Y=46352
X4232 VSS VDD ICV_25 $T=53136 54000 1 0 $X=53136 $Y=52832
X4373 VSS VDD 177 70 88 DFFHQNx1_ASAP7_75t_R $T=4752 32400 1 0 $X=4752 $Y=31232
X4374 VSS VDD 8 71 86 DFFHQNx1_ASAP7_75t_R $T=4752 34560 0 0 $X=4752 $Y=34472
X4375 VSS VDD 8 474 478 DFFHQNx1_ASAP7_75t_R $T=6480 36720 1 0 $X=6480 $Y=35552
X4376 VSS VDD 177 91 122 DFFHQNx1_ASAP7_75t_R $T=8424 28080 0 0 $X=8424 $Y=27992
X4377 VSS VDD 201 115 98 DFFHQNx1_ASAP7_75t_R $T=10368 19440 1 0 $X=10368 $Y=18272
X4378 VSS VDD 177 479 129 DFFHQNx1_ASAP7_75t_R $T=11880 23760 0 0 $X=11880 $Y=23672
X4379 VSS VDD 201 483 622 DFFHQNx1_ASAP7_75t_R $T=14040 12960 0 0 $X=14040 $Y=12872
X4380 VSS VDD 8 646 7 DFFHQNx1_ASAP7_75t_R $T=14256 36720 0 0 $X=14256 $Y=36632
X4381 VSS VDD 8 602 6 DFFHQNx1_ASAP7_75t_R $T=14472 47520 1 0 $X=14472 $Y=46352
X4382 VSS VDD 177 127 606 DFFHQNx1_ASAP7_75t_R $T=14688 21600 0 0 $X=14688 $Y=21512
X4383 VSS VDD 201 145 164 DFFHQNx1_ASAP7_75t_R $T=16848 8640 1 0 $X=16848 $Y=7472
X4384 VSS VDD 177 55 12 DFFHQNx1_ASAP7_75t_R $T=19008 23760 1 0 $X=19008 $Y=22592
X4385 VSS VDD 201 167 569 DFFHQNx1_ASAP7_75t_R $T=19440 12960 0 0 $X=19440 $Y=12872
X4386 VSS VDD 8 623 29 DFFHQNx1_ASAP7_75t_R $T=19440 36720 1 0 $X=19440 $Y=35552
X4387 VSS VDD 201 185 182 DFFHQNx1_ASAP7_75t_R $T=19656 6480 0 0 $X=19656 $Y=6392
X4388 VSS VDD 8 169 5 DFFHQNx1_ASAP7_75t_R $T=19656 41040 0 0 $X=19656 $Y=40952
X4389 VSS VDD 32 493 4 DFFHQNx1_ASAP7_75t_R $T=20736 47520 1 0 $X=20736 $Y=46352
X4390 VSS VDD 177 570 166 DFFHQNx1_ASAP7_75t_R $T=25488 28080 1 0 $X=25488 $Y=26912
X4391 VSS VDD 201 221 232 DFFHQNx1_ASAP7_75t_R $T=25920 12960 1 0 $X=25920 $Y=11792
X4392 VSS VDD 32 224 510 DFFHQNx1_ASAP7_75t_R $T=25920 34560 0 0 $X=25920 $Y=34472
X4393 VSS VDD 177 676 229 DFFHQNx1_ASAP7_75t_R $T=26352 28080 0 0 $X=26352 $Y=27992
X4394 VSS VDD 32 508 242 DFFHQNx1_ASAP7_75t_R $T=27000 36720 0 0 $X=27000 $Y=36632
X4395 VSS VDD 201 651 245 DFFHQNx1_ASAP7_75t_R $T=27216 8640 1 0 $X=27216 $Y=7472
X4396 VSS VDD 32 244 280 DFFHQNx1_ASAP7_75t_R $T=29160 41040 0 0 $X=29160 $Y=40952
X4397 VSS VDD 177 259 285 DFFHQNx1_ASAP7_75t_R $T=29808 23760 1 0 $X=29808 $Y=22592
X4398 VSS VDD 360 296 267 DFFHQNx1_ASAP7_75t_R $T=30456 12960 0 0 $X=30456 $Y=12872
X4399 VSS VDD 177 522 313 DFFHQNx1_ASAP7_75t_R $T=30888 30240 1 0 $X=30888 $Y=29072
X4400 VSS VDD 370 274 297 DFFHQNx1_ASAP7_75t_R $T=31968 21600 0 0 $X=31968 $Y=21512
X4401 VSS VDD 360 527 283 DFFHQNx1_ASAP7_75t_R $T=33480 10800 0 0 $X=33480 $Y=10712
X4402 VSS VDD 360 633 328 DFFHQNx1_ASAP7_75t_R $T=34128 19440 1 0 $X=34128 $Y=18272
X4403 VSS VDD 46 532 349 DFFHQNx1_ASAP7_75t_R $T=36072 49680 0 0 $X=36072 $Y=49592
X4404 VSS VDD 46 334 538 DFFHQNx1_ASAP7_75t_R $T=38016 56160 0 0 $X=38016 $Y=56072
X4405 VSS VDD 48 332 359 DFFHQNx1_ASAP7_75t_R $T=38664 36720 1 0 $X=38664 $Y=35552
X4406 VSS VDD 46 616 365 DFFHQNx1_ASAP7_75t_R $T=38880 54000 1 0 $X=38880 $Y=52832
X4407 VSS VDD 360 346 355 DFFHQNx1_ASAP7_75t_R $T=39312 10800 0 0 $X=39312 $Y=10712
X4408 VSS VDD 370 636 331 DFFHQNx1_ASAP7_75t_R $T=39312 21600 0 0 $X=39312 $Y=21512
X4409 VSS VDD 360 352 574 DFFHQNx1_ASAP7_75t_R $T=39744 15120 0 0 $X=39744 $Y=15032
X4410 VSS VDD 46 638 376 DFFHQNx1_ASAP7_75t_R $T=39744 58320 1 0 $X=39744 $Y=57152
X4411 VSS VDD 360 535 367 DFFHQNx1_ASAP7_75t_R $T=40176 19440 1 0 $X=40176 $Y=18272
X4412 VSS VDD 360 366 396 DFFHQNx1_ASAP7_75t_R $T=42336 12960 0 0 $X=42336 $Y=12872
X4413 VSS VDD 46 540 375 DFFHQNx1_ASAP7_75t_R $T=42336 51840 1 0 $X=42336 $Y=50672
X4414 VSS VDD 48 374 406 DFFHQNx1_ASAP7_75t_R $T=43848 32400 1 0 $X=43848 $Y=31232
X4415 VSS VDD 360 543 394 DFFHQNx1_ASAP7_75t_R $T=44280 10800 1 0 $X=44280 $Y=9632
X4416 VSS VDD 370 542 418 DFFHQNx1_ASAP7_75t_R $T=44928 25920 0 0 $X=44928 $Y=25832
X4417 VSS VDD 48 546 383 DFFHQNx1_ASAP7_75t_R $T=44928 36720 0 0 $X=44928 $Y=36632
X4418 VSS VDD 370 392 427 DFFHQNx1_ASAP7_75t_R $T=45144 23760 0 0 $X=45144 $Y=23672
X4419 VSS VDD 360 397 424 DFFHQNx1_ASAP7_75t_R $T=45576 17280 0 0 $X=45576 $Y=17192
X4420 VSS VDD 370 404 425 DFFHQNx1_ASAP7_75t_R $T=45576 23760 1 0 $X=45576 $Y=22592
X4421 VSS VDD 48 642 399 DFFHQNx1_ASAP7_75t_R $T=47088 34560 0 0 $X=47088 $Y=34472
X4422 VSS VDD 46 659 421 DFFHQNx1_ASAP7_75t_R $T=47088 58320 1 0 $X=47088 $Y=57152
X4423 VSS VDD 370 555 439 DFFHQNx1_ASAP7_75t_R $T=48168 19440 1 0 $X=48168 $Y=18272
X4424 VSS VDD 46 415 408 DFFHQNx1_ASAP7_75t_R $T=48816 54000 1 0 $X=48816 $Y=52832
X4425 VSS VDD 360 557 410 DFFHQNx1_ASAP7_75t_R $T=49464 12960 1 0 $X=49464 $Y=11792
X4426 VSS VDD 370 615 451 DFFHQNx1_ASAP7_75t_R $T=50544 30240 0 0 $X=50544 $Y=30152
X4427 VSS VDD 48 435 464 DFFHQNx1_ASAP7_75t_R $T=51624 32400 1 0 $X=51624 $Y=31232
X4428 VSS VDD 360 562 442 DFFHQNx1_ASAP7_75t_R $T=51840 17280 1 0 $X=51840 $Y=16112
X4429 VSS VDD 370 563 448 DFFHQNx1_ASAP7_75t_R $T=51840 25920 1 0 $X=51840 $Y=24752
X4430 VSS VDD 370 368 457 DFFHQNx1_ASAP7_75t_R $T=52056 28080 0 0 $X=52056 $Y=27992
X4431 VSS VDD 370 675 431 DFFHQNx1_ASAP7_75t_R $T=52272 21600 1 0 $X=52272 $Y=20432
X4432 VSS VDD 46 438 553 DFFHQNx1_ASAP7_75t_R $T=52272 51840 1 0 $X=52272 $Y=50672
X4433 VSS VDD 360 441 447 DFFHQNx1_ASAP7_75t_R $T=52920 12960 0 0 $X=52920 $Y=12872
X4434 VSS VDD 201 608 128 ICV_27 $T=12528 15120 1 0 $X=12528 $Y=13952
X4435 VSS VDD 177 677 481 ICV_27 $T=12744 28080 0 0 $X=12744 $Y=27992
X4436 VSS VDD 201 179 217 ICV_27 $T=22032 15120 1 0 $X=22032 $Y=13952
X4437 VSS VDD 360 278 289 ICV_27 $T=31968 6480 0 0 $X=31968 $Y=6392
X4438 VSS VDD 48 678 362 ICV_27 $T=38232 43200 0 0 $X=38232 $Y=43112
X4439 VSS VDD 48 560 414 ICV_27 $T=50112 38880 0 0 $X=50112 $Y=38792
X4440 VSS VDD 46 564 443 ICV_27 $T=50976 49680 1 0 $X=50976 $Y=48512
X4441 VSS VDD 370 450 437 ICV_27 $T=52920 23760 1 0 $X=52920 $Y=22592
X4641 VSS VDD 76 66 474 AND2x2_ASAP7_75t_R $T=6912 36720 0 0 $X=6912 $Y=36632
X4642 VSS VDD 92 82 99 AND2x2_ASAP7_75t_R $T=9288 34560 1 0 $X=9288 $Y=33392
X4643 VSS VDD 97 66 91 AND2x2_ASAP7_75t_R $T=10152 28080 1 0 $X=10152 $Y=26912
X4644 VSS VDD 107 66 115 AND2x2_ASAP7_75t_R $T=11664 17280 0 0 $X=11664 $Y=17192
X4645 VSS VDD 111 66 479 AND2x2_ASAP7_75t_R $T=11664 23760 1 0 $X=11664 $Y=22592
X4646 VSS VDD 22 484 116 AND2x2_ASAP7_75t_R $T=11880 30240 1 0 $X=11880 $Y=29072
X4647 VSS VDD 122 481 17 AND2x2_ASAP7_75t_R $T=14256 28080 1 0 $X=14256 $Y=26912
X4648 VSS VDD 58 138 172 AND2x2_ASAP7_75t_R $T=14904 32400 0 0 $X=14904 $Y=32312
X4649 VSS VDD 138 144 196 AND2x2_ASAP7_75t_R $T=17928 19440 0 0 $X=17928 $Y=19352
X4650 VSS VDD 17 22 159 AND2x2_ASAP7_75t_R $T=18144 30240 1 0 $X=18144 $Y=29072
X4651 VSS VDD 58 94 567 AND2x2_ASAP7_75t_R $T=19440 23760 0 0 $X=19440 $Y=23672
X4652 VSS VDD 151 166 15 AND2x2_ASAP7_75t_R $T=20520 28080 1 0 $X=20520 $Y=26912
X4653 VSS VDD 498 165 156 AND2x2_ASAP7_75t_R $T=21600 17280 1 0 $X=21600 $Y=16112
X4654 VSS VDD 243 12 498 AND2x2_ASAP7_75t_R $T=23328 19440 0 0 $X=23328 $Y=19352
X4655 VSS VDD 133 24 210 AND2x2_ASAP7_75t_R $T=24408 32400 0 0 $X=24408 $Y=32312
X4656 VSS VDD 269 634 683 AND2x2_ASAP7_75t_R $T=33264 49680 1 0 $X=33264 $Y=48512
X4657 VSS VDD 298 323 311 AND2x2_ASAP7_75t_R $T=34776 38880 0 0 $X=34776 $Y=38792
X4658 VSS VDD 292 318 634 AND2x2_ASAP7_75t_R $T=35640 34560 0 0 $X=35640 $Y=34472
X4659 VSS VDD 310 342 531 AND2x2_ASAP7_75t_R $T=36072 41040 1 0 $X=36072 $Y=39872
X4660 VSS VDD 337 303 351 AND2x2_ASAP7_75t_R $T=39312 32400 1 0 $X=39312 $Y=31232
X4661 VSS VDD 347 66 636 AND2x2_ASAP7_75t_R $T=39744 21600 1 0 $X=39744 $Y=20432
X4662 VSS VDD 335 529 40 AND2x2_ASAP7_75t_R $T=41472 47520 1 0 $X=41472 $Y=46352
X4663 VSS VDD 653 66 540 AND2x2_ASAP7_75t_R $T=42336 49680 0 0 $X=42336 $Y=49592
X4664 VSS VDD 407 375 657 AND2x2_ASAP7_75t_R $T=45144 49680 1 0 $X=45144 $Y=48512
X4665 VSS VDD 402 380 640 AND2x2_ASAP7_75t_R $T=45792 43200 1 0 $X=45792 $Y=42032
X4666 VSS VDD 393 400 388 AND2x2_ASAP7_75t_R $T=46008 47520 1 0 $X=46008 $Y=46352
X4667 VSS VDD 395 66 642 AND2x2_ASAP7_75t_R $T=46872 34560 1 0 $X=46872 $Y=33392
X4668 VSS VDD 399 406 419 AND2x2_ASAP7_75t_R $T=47952 36720 1 0 $X=47952 $Y=35552
X4669 VSS VDD 428 66 435 AND2x2_ASAP7_75t_R $T=50328 32400 1 0 $X=50328 $Y=31232
X4670 VSS VDD 419 402 552 AND2x2_ASAP7_75t_R $T=50328 43200 0 0 $X=50328 $Y=43112
X4671 VSS VDD 643 66 438 AND2x2_ASAP7_75t_R $T=50976 51840 1 0 $X=50976 $Y=50672
X4672 VSS VDD 433 342 644 AND2x2_ASAP7_75t_R $T=51624 45360 1 0 $X=51624 $Y=44192
X4673 VSS VDD 436 66 564 AND2x2_ASAP7_75t_R $T=51840 47520 1 0 $X=51840 $Y=46352
X4674 VSS VDD ICV_35 $T=4752 12960 0 0 $X=4752 $Y=12872
X4675 VSS VDD ICV_35 $T=4752 15120 1 0 $X=4752 $Y=13952
X4676 VSS VDD ICV_35 $T=4752 15120 0 0 $X=4752 $Y=15032
X4677 VSS VDD ICV_35 $T=4752 47520 0 0 $X=4752 $Y=47432
X4678 VSS VDD ICV_35 $T=14256 51840 0 0 $X=14256 $Y=51752
X4679 VSS VDD ICV_35 $T=14256 54000 0 0 $X=14256 $Y=53912
X4680 VSS VDD ICV_35 $T=14256 56160 1 0 $X=14256 $Y=54992
X4681 VSS VDD ICV_35 $T=16200 32400 0 0 $X=16200 $Y=32312
X4682 VSS VDD ICV_35 $T=18576 10800 0 0 $X=18576 $Y=10712
X4683 VSS VDD ICV_35 $T=22680 25920 1 0 $X=22680 $Y=24752
X4684 VSS VDD ICV_35 $T=23544 10800 1 0 $X=23544 $Y=9632
X4685 VSS VDD ICV_35 $T=23760 58320 0 0 $X=23760 $Y=58232
X4686 VSS VDD ICV_35 $T=27648 43200 0 0 $X=27648 $Y=43112
X4687 VSS VDD ICV_35 $T=29592 36720 1 0 $X=29592 $Y=35552
X4688 VSS VDD ICV_35 $T=32400 32400 0 0 $X=32400 $Y=32312
X4689 VSS VDD ICV_35 $T=33480 17280 1 0 $X=33480 $Y=16112
X4690 VSS VDD ICV_35 $T=34992 10800 1 0 $X=34992 $Y=9632
X4691 VSS VDD ICV_35 $T=39960 30240 0 0 $X=39960 $Y=30152
X4692 VSS VDD ICV_35 $T=50976 15120 0 0 $X=50976 $Y=15032
X4693 VSS VDD ICV_35 $T=51192 32400 0 0 $X=51192 $Y=32312
X4694 VSS VDD ICV_35 $T=51192 49680 0 0 $X=51192 $Y=49592
X4695 VSS VDD ICV_35 $T=51408 34560 0 0 $X=51408 $Y=34472
X4696 VSS VDD ICV_35 $T=51408 58320 1 0 $X=51408 $Y=57152
X4697 VSS VDD ICV_35 $T=51840 4320 0 0 $X=51840 $Y=4232
X4698 VSS VDD ICV_35 $T=51840 8640 0 0 $X=51840 $Y=8552
X4699 VSS VDD ICV_35 $T=51840 41040 1 0 $X=51840 $Y=39872
X4700 VSS VDD 86 83 92 XNOR2x2_ASAP7_75t_R $T=6912 34560 1 0 $X=6912 $Y=33392
X4701 VSS VDD 77 104 601 XNOR2x2_ASAP7_75t_R $T=7128 41040 0 0 $X=7128 $Y=40786
X4702 VSS VDD 75 662 476 XNOR2x2_ASAP7_75t_R $T=7560 47520 1 0 $X=7560 $Y=46352
X4703 VSS VDD 100 583 301 XNOR2x2_ASAP7_75t_R $T=10800 43200 0 0 $X=10800 $Y=42946
X4704 VSS VDD 122 117 618 XNOR2x2_ASAP7_75t_R $T=12096 25920 0 0 $X=12096 $Y=25666
X4705 VSS VDD 119 621 124 XNOR2x2_ASAP7_75t_R $T=12096 47520 1 0 $X=12096 $Y=46352
X4706 VSS VDD 119 480 319 XNOR2x2_ASAP7_75t_R $T=13824 45360 0 0 $X=13824 $Y=45106
X4707 VSS VDD 207 488 170 XNOR2x2_ASAP7_75t_R $T=18360 49680 0 0 $X=18360 $Y=49426
X4708 VSS VDD 178 181 664 XNOR2x2_ASAP7_75t_R $T=20736 49680 1 0 $X=20736 $Y=48512
X4709 VSS VDD 183 626 216 XNOR2x2_ASAP7_75t_R $T=24192 56160 1 0 $X=24192 $Y=54992
X4710 VSS VDD 227 215 213 XNOR2x2_ASAP7_75t_R $T=25272 43200 0 0 $X=25272 $Y=42946
X4711 VSS VDD 34 627 666 XNOR2x2_ASAP7_75t_R $T=26352 45360 1 0 $X=26352 $Y=44192
X4712 VSS VDD 225 509 240 XNOR2x2_ASAP7_75t_R $T=26352 47520 1 0 $X=26352 $Y=46352
X4713 VSS VDD 178 219 241 XNOR2x2_ASAP7_75t_R $T=26568 51840 0 0 $X=26568 $Y=51586
X4714 VSS VDD 242 238 248 XNOR2x2_ASAP7_75t_R $T=27216 36720 1 0 $X=27216 $Y=35552
X4715 VSS VDD 207 30 249 XNOR2x2_ASAP7_75t_R $T=27216 49680 0 0 $X=27216 $Y=49426
X4716 VSS VDD 252 118 256 XNOR2x2_ASAP7_75t_R $T=28080 47520 0 0 $X=28080 $Y=47266
X4717 VSS VDD 246 226 518 XNOR2x2_ASAP7_75t_R $T=28296 51840 1 0 $X=28296 $Y=50672
X4718 VSS VDD 227 62 288 XNOR2x2_ASAP7_75t_R $T=30456 45360 0 0 $X=30456 $Y=45106
X4719 VSS VDD 225 230 276 XNOR2x2_ASAP7_75t_R $T=30672 51840 1 0 $X=30672 $Y=50672
X4720 VSS VDD 349 681 307 XNOR2x2_ASAP7_75t_R $T=39960 51840 1 0 $X=39960 $Y=50672
X4721 VSS VDD 348 357 298 XNOR2x2_ASAP7_75t_R $T=40392 38880 1 0 $X=40392 $Y=37712
X4722 VSS VDD 359 537 42 XNOR2x2_ASAP7_75t_R $T=40824 36720 0 0 $X=40824 $Y=36466
X4723 VSS VDD 538 554 324 XNOR2x2_ASAP7_75t_R $T=41472 56160 1 0 $X=41472 $Y=54992
X4724 VSS VDD 365 377 339 XNOR2x2_ASAP7_75t_R $T=43200 54000 1 0 $X=43200 $Y=52832
X4725 VSS VDD 362 655 391 XNOR2x2_ASAP7_75t_R $T=43632 45360 0 0 $X=43632 $Y=45106
X4726 VSS VDD 408 682 411 XNOR2x2_ASAP7_75t_R $T=46008 51840 0 0 $X=46008 $Y=51586
X4727 VSS VDD 421 409 548 XNOR2x2_ASAP7_75t_R $T=47520 56160 1 0 $X=47520 $Y=54992
X4728 VSS VDD 553 578 680 XNOR2x2_ASAP7_75t_R $T=48600 49680 1 0 $X=48600 $Y=48512
X4729 VSS VDD 456 440 429 XNOR2x2_ASAP7_75t_R $T=50544 43200 1 0 $X=50544 $Y=42032
X4730 VSS VDD 443 430 446 XNOR2x2_ASAP7_75t_R $T=51624 47520 0 0 $X=51624 $Y=47266
X4731 VSS VDD 177 72 619 ICV_36 $T=4752 23760 0 0 $X=4752 $Y=23672
X4732 VSS VDD 32 194 26 ICV_36 $T=22464 36720 0 0 $X=22464 $Y=36632
X4733 VSS VDD 201 195 501 ICV_36 $T=22680 8640 1 0 $X=22680 $Y=7472
X4734 VSS VDD 201 212 243 ICV_36 $T=24624 19440 0 0 $X=24624 $Y=19352
X4735 VSS VDD 360 257 251 ICV_36 $T=29376 17280 0 0 $X=29376 $Y=17192
X4736 VSS VDD 32 531 348 ICV_36 $T=35856 38880 1 0 $X=35856 $Y=37712
X4737 VSS VDD 46 317 369 ICV_36 $T=38880 58320 0 0 $X=38880 $Y=58232
X4738 VSS VDD 32 637 372 ICV_36 $T=39096 38880 0 0 $X=39096 $Y=38792
X4739 VSS VDD 360 558 444 ICV_36 $T=49032 15120 1 0 $X=49032 $Y=13952
X4740 VSS VDD 48 644 456 ICV_36 $T=51624 43200 0 0 $X=51624 $Y=43112
X4741 VSS VDD 98 129 619 475 AND3x1_ASAP7_75t_R $T=9288 30240 1 0 $X=9288 $Y=29072
X4742 VSS VDD 619 88 484 83 AND3x1_ASAP7_75t_R $T=9288 30240 0 0 $X=9288 $Y=30030
X4743 VSS VDD 88 86 475 106 AND3x1_ASAP7_75t_R $T=10152 32400 0 0 $X=10152 $Y=32190
X4744 VSS VDD 144 138 96 109 AND3x1_ASAP7_75t_R $T=10368 38880 1 0 $X=10368 $Y=37712
X4745 VSS VDD 484 22 17 133 AND3x1_ASAP7_75t_R $T=15552 28080 1 0 $X=15552 $Y=26912
X4746 VSS VDD 6 152 134 485 AND3x1_ASAP7_75t_R $T=15984 45360 1 0 $X=15984 $Y=44192
X4747 VSS VDD 136 135 156 146 AND3x1_ASAP7_75t_R $T=16632 12960 1 0 $X=16632 $Y=11792
X4748 VSS VDD 611 165 498 94 AND3x1_ASAP7_75t_R $T=19440 19440 1 0 $X=19440 $Y=18272
X4749 VSS VDD 648 144 16 647 AND3x1_ASAP7_75t_R $T=20088 17280 0 0 $X=20088 $Y=17070
X4750 VSS VDD 29 23 131 180 AND3x1_ASAP7_75t_R $T=21168 36720 0 0 $X=21168 $Y=36510
X4751 VSS VDD 569 217 243 186 AND3x1_ASAP7_75t_R $T=22680 19440 1 0 $X=22680 $Y=18272
X4752 VSS VDD 497 614 498 104 AND3x1_ASAP7_75t_R $T=23544 17280 0 0 $X=23544 $Y=17070
X4753 VSS VDD 229 24 131 238 AND3x1_ASAP7_75t_R $T=26352 34560 1 0 $X=26352 $Y=33392
X4754 VSS VDD 66 144 138 235 AND3x1_ASAP7_75t_R $T=27000 38880 0 0 $X=27000 $Y=38670
X4755 VSS VDD 229 242 210 262 AND3x1_ASAP7_75t_R $T=28080 32400 0 0 $X=28080 $Y=32190
X4756 VSS VDD 517 523 247 224 AND3x1_ASAP7_75t_R $T=30888 34560 0 0 $X=30888 $Y=34350
X4757 VSS VDD 266 318 292 271 AND3x1_ASAP7_75t_R $T=31320 41040 1 0 $X=31320 $Y=39872
X4758 VSS VDD 66 529 335 344 AND3x1_ASAP7_75t_R $T=38664 51840 1 0 $X=38664 $Y=50672
X4759 VSS VDD 300 343 303 350 AND3x1_ASAP7_75t_R $T=39312 30240 1 0 $X=39312 $Y=29072
X4760 VSS VDD 406 399 372 537 AND3x1_ASAP7_75t_R $T=42984 36720 1 0 $X=42984 $Y=35552
X4761 VSS VDD 538 369 554 377 AND3x1_ASAP7_75t_R $T=42984 54000 0 0 $X=42984 $Y=53790
X4762 VSS VDD 372 359 380 357 AND3x1_ASAP7_75t_R $T=43632 38880 0 0 $X=43632 $Y=38670
X4763 VSS VDD 66 403 575 542 AND3x1_ASAP7_75t_R $T=44496 28080 1 0 $X=44496 $Y=26912
X4764 VSS VDD 380 402 400 407 AND3x1_ASAP7_75t_R $T=44928 43200 0 0 $X=44928 $Y=42990
X4765 VSS VDD 375 408 407 409 AND3x1_ASAP7_75t_R $T=46872 54000 0 0 $X=46872 $Y=53790
X4766 VSS VDD 375 400 552 682 AND3x1_ASAP7_75t_R $T=47304 49680 1 0 $X=47304 $Y=48512
X4767 VSS VDD 414 456 640 430 AND3x1_ASAP7_75t_R $T=49680 47520 0 0 $X=49680 $Y=47310
X4768 VSS VDD 66 559 641 560 AND3x1_ASAP7_75t_R $T=50544 41040 1 0 $X=50544 $Y=39872
X4769 VSS VDD 414 402 419 440 AND3x1_ASAP7_75t_R $T=51408 41040 0 0 $X=51408 $Y=40830
X4770 VSS VDD 95 58 103 66 72 OA211x2_ASAP7_75t_R $T=9936 23760 1 0 $X=9936 $Y=22592
X4771 VSS VDD 130 7 125 147 646 OA211x2_ASAP7_75t_R $T=16200 36720 1 0 $X=16200 $Y=35552
X4772 VSS VDD 663 485 645 66 602 OA211x2_ASAP7_75t_R $T=16200 45360 0 0 $X=16200 $Y=45272
X4773 VSS VDD 487 144 152 4 163 OA211x2_ASAP7_75t_R $T=18360 43200 0 0 $X=18360 $Y=43112
X4774 VSS VDD 684 610 685 66 623 OA211x2_ASAP7_75t_R $T=18792 34560 0 0 $X=18792 $Y=34472
X4775 VSS VDD 162 612 153 66 169 OA211x2_ASAP7_75t_R $T=18792 41040 1 0 $X=18792 $Y=39872
X4776 VSS VDD 196 192 649 66 167 OA211x2_ASAP7_75t_R $T=23760 12960 0 0 $X=23760 $Y=12872
X4777 VSS VDD 208 144 624 229 222 OA211x2_ASAP7_75t_R $T=25272 30240 1 0 $X=25272 $Y=29072
X4778 VSS VDD 196 232 505 66 228 OA211x2_ASAP7_75t_R $T=25488 12960 0 0 $X=25488 $Y=12872
X4779 VSS VDD 286 314 277 66 274 OA211x2_ASAP7_75t_R $T=32400 23760 0 0 $X=32400 $Y=23672
X4780 VSS VDD 307 326 302 342 532 OA211x2_ASAP7_75t_R $T=35424 51840 1 0 $X=35424 $Y=50672
X4781 VSS VDD 319 292 600 529 432 OA211x2_ASAP7_75t_R $T=37152 45360 1 0 $X=37152 $Y=44192
X4782 VSS VDD 324 529 309 342 334 OA211x2_ASAP7_75t_R $T=37584 56160 1 0 $X=37584 $Y=54992
X4783 VSS VDD 654 326 330 66 637 OA211x2_ASAP7_75t_R $T=38664 41040 1 0 $X=38664 $Y=39872
X4784 VSS VDD 351 291 529 406 361 OA211x2_ASAP7_75t_R $T=40176 32400 0 0 $X=40176 $Y=32312
X4785 VSS VDD 434 356 603 66 346 OA211x2_ASAP7_75t_R $T=41256 12960 1 0 $X=41256 $Y=11792
X4786 VSS VDD 434 382 656 66 543 OA211x2_ASAP7_75t_R $T=44496 12960 1 0 $X=44496 $Y=11792
X4787 VSS VDD 378 376 658 342 669 OA211x2_ASAP7_75t_R $T=45144 56160 0 0 $X=45144 $Y=56072
X4788 VSS VDD 548 529 308 342 659 OA211x2_ASAP7_75t_R $T=46872 56160 0 0 $X=46872 $Y=56072
X4789 VSS VDD 411 529 304 342 415 OA211x2_ASAP7_75t_R $T=47088 54000 1 0 $X=47088 $Y=52832
X4790 VSS VDD 434 422 660 66 557 OA211x2_ASAP7_75t_R $T=48816 12960 0 0 $X=48816 $Y=12872
X4791 VSS VDD 434 423 452 66 441 OA211x2_ASAP7_75t_R $T=51192 12960 0 0 $X=51192 $Y=12872
X4792 VSS VDD 434 345 561 66 675 OA211x2_ASAP7_75t_R $T=51192 19440 0 0 $X=51192 $Y=19352
X4793 VSS VDD 434 389 671 66 450 OA211x2_ASAP7_75t_R $T=51192 23760 1 0 $X=51192 $Y=22592
X4794 VSS VDD 434 617 449 66 562 OA211x2_ASAP7_75t_R $T=51408 17280 0 0 $X=51408 $Y=17192
X4795 VSS VDD 434 412 453 66 563 OA211x2_ASAP7_75t_R $T=51408 23760 0 0 $X=51408 $Y=23672
X4987 VSS VDD 196 601 89 74 AO21x1_ASAP7_75t_R $T=8424 32400 0 0 $X=8424 $Y=32238
X4988 VSS VDD 84 104 93 96 AO21x1_ASAP7_75t_R $T=8856 38880 0 0 $X=8856 $Y=38718
X4989 VSS VDD 98 82 112 107 AO21x1_ASAP7_75t_R $T=10368 17280 0 0 $X=10368 $Y=17118
X4990 VSS VDD 102 82 620 111 AO21x1_ASAP7_75t_R $T=10584 21600 0 0 $X=10584 $Y=21438
X4991 VSS VDD 7 131 144 134 AO21x1_ASAP7_75t_R $T=15768 43200 1 0 $X=15768 $Y=42032
X4992 VSS VDD 82 133 157 147 AO21x1_ASAP7_75t_R $T=16848 34560 0 0 $X=16848 $Y=34398
X4993 VSS VDD 128 16 146 150 AO21x1_ASAP7_75t_R $T=17280 15120 1 0 $X=17280 $Y=13952
X4994 VSS VDD 586 156 486 154 AO21x1_ASAP7_75t_R $T=17712 8640 0 0 $X=17712 $Y=8478
X4995 VSS VDD 196 170 141 162 AO21x1_ASAP7_75t_R $T=18576 38880 1 0 $X=18576 $Y=37712
X4996 VSS VDD 489 156 673 175 AO21x1_ASAP7_75t_R $T=20520 8640 0 0 $X=20520 $Y=8478
X4997 VSS VDD 12 613 499 14 AO21x1_ASAP7_75t_R $T=20952 21600 1 0 $X=20952 $Y=20432
X4998 VSS VDD 587 156 496 203 AO21x1_ASAP7_75t_R $T=23328 6480 1 0 $X=23328 $Y=5312
X4999 VSS VDD 138 213 82 220 AO21x1_ASAP7_75t_R $T=25704 30240 0 0 $X=25704 $Y=30078
X5000 VSS VDD 138 666 82 231 AO21x1_ASAP7_75t_R $T=26352 41040 0 0 $X=26352 $Y=40878
X5001 VSS VDD 506 156 650 233 AO21x1_ASAP7_75t_R $T=26784 6480 1 0 $X=26784 $Y=5312
X5002 VSS VDD 138 518 82 247 AO21x1_ASAP7_75t_R $T=28296 38880 0 0 $X=28296 $Y=38718
X5003 VSS VDD 516 156 628 264 AO21x1_ASAP7_75t_R $T=30456 15120 1 0 $X=30456 $Y=13952
X5004 VSS VDD 519 156 629 268 AO21x1_ASAP7_75t_R $T=30888 6480 1 0 $X=30888 $Y=5312
X5005 VSS VDD 266 329 524 41 AO21x1_ASAP7_75t_R $T=31536 38880 1 0 $X=31536 $Y=37712
X5006 VSS VDD 595 156 525 281 AO21x1_ASAP7_75t_R $T=32400 10800 1 0 $X=32400 $Y=9632
X5007 VSS VDD 279 634 323 293 AO21x1_ASAP7_75t_R $T=33264 32400 1 0 $X=33264 $Y=31232
X5008 VSS VDD 282 156 290 294 AO21x1_ASAP7_75t_R $T=33480 8640 1 0 $X=33480 $Y=7472
X5009 VSS VDD 329 301 271 295 AO21x1_ASAP7_75t_R $T=33480 41040 1 0 $X=33480 $Y=39872
X5010 VSS VDD 326 295 311 310 AO21x1_ASAP7_75t_R $T=34776 41040 1 0 $X=34776 $Y=39872
X5011 VSS VDD 313 533 320 318 AO21x1_ASAP7_75t_R $T=36720 25920 0 0 $X=36720 $Y=25758
X5012 VSS VDD 335 679 323 330 AO21x1_ASAP7_75t_R $T=37368 41040 1 0 $X=37368 $Y=39872
X5013 VSS VDD 300 533 353 337 AO21x1_ASAP7_75t_R $T=38232 28080 1 0 $X=38232 $Y=26912
X5014 VSS VDD 313 533 331 354 AO21x1_ASAP7_75t_R $T=40176 23760 0 0 $X=40176 $Y=23598
X5015 VSS VDD 639 323 387 395 AO21x1_ASAP7_75t_R $T=45360 34560 1 0 $X=45360 $Y=33392
X5016 VSS VDD 329 529 398 403 AO21x1_ASAP7_75t_R $T=45792 28080 1 0 $X=45792 $Y=26912
X5017 VSS VDD 43 429 432 433 AO21x1_ASAP7_75t_R $T=50328 45360 1 0 $X=50328 $Y=44192
X5046 VSS VDD ICV_48 $T=4752 17280 1 0 $X=4752 $Y=16112
X5047 VSS VDD ICV_48 $T=4752 21600 1 0 $X=4752 $Y=20432
X5048 VSS VDD ICV_48 $T=4752 49680 1 0 $X=4752 $Y=48512
X5049 VSS VDD ICV_48 $T=10584 32400 1 0 $X=10584 $Y=31232
X5050 VSS VDD ICV_48 $T=11232 25920 1 0 $X=11232 $Y=24752
X5051 VSS VDD ICV_48 $T=14256 54000 1 0 $X=14256 $Y=52832
X5052 VSS VDD ICV_48 $T=19008 43200 1 0 $X=19008 $Y=42032
X5053 VSS VDD ICV_48 $T=26784 15120 0 0 $X=26784 $Y=15032
X5054 VSS VDD ICV_48 $T=27216 21600 1 0 $X=27216 $Y=20432
X5055 VSS VDD ICV_48 $T=29160 19440 0 0 $X=29160 $Y=19352
X5056 VSS VDD ICV_48 $T=30024 60480 1 0 $X=30024 $Y=59312
X5057 VSS VDD ICV_48 $T=30888 34560 1 0 $X=30888 $Y=33392
X5058 VSS VDD ICV_48 $T=32832 4320 0 0 $X=32832 $Y=4232
X5059 VSS VDD ICV_48 $T=35424 6480 1 0 $X=35424 $Y=5312
X5060 VSS VDD ICV_48 $T=36720 6480 0 0 $X=36720 $Y=6392
X5061 VSS VDD ICV_48 $T=39528 60480 1 0 $X=39528 $Y=59312
X5062 VSS VDD ICV_48 $T=39960 28080 0 0 $X=39960 $Y=27992
X5063 VSS VDD ICV_48 $T=42336 4320 0 0 $X=42336 $Y=4232
X5064 VSS VDD ICV_48 $T=42336 8640 0 0 $X=42336 $Y=8552
X5065 VSS VDD ICV_48 $T=43416 30240 1 0 $X=43416 $Y=29072
X5066 VSS VDD ICV_48 $T=43416 58320 0 0 $X=43416 $Y=58232
X5067 VSS VDD ICV_48 $T=46008 45360 0 0 $X=46008 $Y=45272
X5068 VSS VDD ICV_48 $T=46224 6480 0 0 $X=46224 $Y=6392
X5069 VSS VDD ICV_48 $T=46440 10800 0 0 $X=46440 $Y=10712
X5070 VSS VDD ICV_48 $T=46656 38880 1 0 $X=46656 $Y=37712
X5071 VSS VDD ICV_48 $T=46872 8640 1 0 $X=46872 $Y=7472
X5072 VSS VDD ICV_48 $T=47088 28080 1 0 $X=47088 $Y=26912
X5073 VSS VDD ICV_48 $T=47520 6480 1 0 $X=47520 $Y=5312
X5074 VSS VDD ICV_48 $T=48168 34560 1 0 $X=48168 $Y=33392
X5075 VSS VDD ICV_48 $T=48384 51840 0 0 $X=48384 $Y=51752
X5076 VSS VDD ICV_48 $T=48600 10800 1 0 $X=48600 $Y=9632
X5077 VSS VDD ICV_48 $T=48600 56160 0 0 $X=48600 $Y=56072
X5078 VSS VDD ICV_48 $T=49032 60480 1 0 $X=49032 $Y=59312
X5079 VSS VDD ICV_48 $T=49248 36720 1 0 $X=49248 $Y=35552
X5080 VSS VDD ICV_48 $T=49248 36720 0 0 $X=49248 $Y=36632
X5081 VSS VDD ICV_48 $T=49680 54000 0 0 $X=49680 $Y=53912
X5082 VSS VDD ICV_48 $T=49896 56160 1 0 $X=49896 $Y=54992
X5083 VSS VDD 82 618 196 476 97 AO22x1_ASAP7_75t_R $T=8208 28080 1 0 $X=8208 $Y=26912
X5084 VSS VDD 323 552 322 40 390 AO22x1_ASAP7_75t_R $T=43848 41040 0 0 $X=43848 $Y=40862
X5085 VSS VDD 40 632 323 680 643 AO22x1_ASAP7_75t_R $T=49248 49680 0 0 $X=49248 $Y=49502
X5086 VSS VDD 323 446 40 605 436 AO22x1_ASAP7_75t_R $T=49896 47520 1 0 $X=49896 $Y=46352
X5174 74 VSS VDD 80 66 70 OA21x2_ASAP7_75t_R $T=6264 32400 0 0 $X=6264 $Y=32312
X5175 99 VSS VDD 109 66 71 OA21x2_ASAP7_75t_R $T=10800 36720 1 0 $X=10800 $Y=35552
X5176 126 VSS VDD 292 318 679 OA21x2_ASAP7_75t_R $T=15120 41040 1 0 $X=15120 $Y=39872
X5177 104 VSS VDD 118 252 566 OA21x2_ASAP7_75t_R $T=15336 49680 1 0 $X=15336 $Y=48512
X5178 82 VSS VDD 16 606 148 OA21x2_ASAP7_75t_R $T=16632 21600 1 0 $X=16632 $Y=20432
X5179 173 VSS VDD 154 670 145 OA21x2_ASAP7_75t_R $T=19008 8640 0 0 $X=19008 $Y=8552
X5180 10 VSS VDD 664 66 674 OA21x2_ASAP7_75t_R $T=20304 47520 0 0 $X=20304 $Y=47432
X5181 168 VSS VDD 647 66 179 OA21x2_ASAP7_75t_R $T=20520 15120 1 0 $X=20520 $Y=13952
X5182 163 VSS VDD 161 674 493 OA21x2_ASAP7_75t_R $T=20520 45360 0 0 $X=20520 $Y=45272
X5183 173 VSS VDD 175 191 185 OA21x2_ASAP7_75t_R $T=21168 8640 1 0 $X=21168 $Y=7472
X5184 567 VSS VDD 188 26 202 OA21x2_ASAP7_75t_R $T=22896 34560 1 0 $X=22896 $Y=33392
X5185 173 VSS VDD 203 206 195 OA21x2_ASAP7_75t_R $T=23976 6480 0 0 $X=23976 $Y=6392
X5186 198 VSS VDD 208 82 502 OA21x2_ASAP7_75t_R $T=23976 28080 1 0 $X=23976 $Y=26912
X5187 500 VSS VDD 665 251 503 OA21x2_ASAP7_75t_R $T=24840 17280 0 0 $X=24840 $Y=17192
X5188 200 VSS VDD 197 66 212 OA21x2_ASAP7_75t_R $T=25704 21600 1 0 $X=25704 $Y=20432
X5189 173 VSS VDD 233 237 651 OA21x2_ASAP7_75t_R $T=27648 6480 0 0 $X=27648 $Y=6392
X5190 173 VSS VDD 264 258 257 OA21x2_ASAP7_75t_R $T=30888 17280 1 0 $X=30888 $Y=16112
X5191 173 VSS VDD 268 305 278 OA21x2_ASAP7_75t_R $T=32616 6480 1 0 $X=32616 $Y=5312
X5192 273 VSS VDD 329 318 286 OA21x2_ASAP7_75t_R $T=32616 25920 0 0 $X=32616 $Y=25832
X5193 173 VSS VDD 281 631 296 OA21x2_ASAP7_75t_R $T=33480 12960 1 0 $X=33480 $Y=11792
X5194 173 VSS VDD 294 528 527 OA21x2_ASAP7_75t_R $T=33912 8640 0 0 $X=33912 $Y=8552
X5195 328 VSS VDD 300 315 635 OA21x2_ASAP7_75t_R $T=37152 30240 1 0 $X=37152 $Y=29072
X5196 341 VSS VDD 333 529 338 OA21x2_ASAP7_75t_R $T=38016 25920 1 0 $X=38016 $Y=24752
X5197 43 VSS VDD 299 668 678 OA21x2_ASAP7_75t_R $T=38880 45360 1 0 $X=38880 $Y=44192
X5198 326 VSS VDD 391 342 668 OA21x2_ASAP7_75t_R $T=38880 45360 0 0 $X=38880 $Y=45272
X5199 323 VSS VDD 335 66 342 OA21x2_ASAP7_75t_R $T=39312 47520 0 0 $X=39312 $Y=47432
X5200 683 VSS VDD 316 669 638 OA21x2_ASAP7_75t_R $T=39312 56160 1 0 $X=39312 $Y=54992
X5201 345 VSS VDD 363 364 535 OA21x2_ASAP7_75t_R $T=39960 19440 0 0 $X=39960 $Y=19352
X5202 634 VSS VDD 351 529 358 OA21x2_ASAP7_75t_R $T=40824 32400 1 0 $X=40824 $Y=31232
X5203 356 VSS VDD 363 573 352 OA21x2_ASAP7_75t_R $T=41040 15120 1 0 $X=41040 $Y=13952
X5204 406 VSS VDD 358 371 374 OA21x2_ASAP7_75t_R $T=42336 32400 1 0 $X=42336 $Y=31232
X5205 382 VSS VDD 363 604 366 OA21x2_ASAP7_75t_R $T=44496 15120 1 0 $X=44496 $Y=13952
X5206 385 VSS VDD 390 544 546 OA21x2_ASAP7_75t_R $T=45144 38880 1 0 $X=45144 $Y=37712
X5207 389 VSS VDD 363 547 404 OA21x2_ASAP7_75t_R $T=45576 21600 0 0 $X=45576 $Y=21512
X5208 422 VSS VDD 363 687 397 OA21x2_ASAP7_75t_R $T=46440 17280 1 0 $X=46440 $Y=16112
X5209 412 VSS VDD 363 417 392 OA21x2_ASAP7_75t_R $T=48384 25920 1 0 $X=48384 $Y=24752
X5210 617 VSS VDD 363 661 555 OA21x2_ASAP7_75t_R $T=48600 19440 0 0 $X=48600 $Y=19352
X5211 423 VSS VDD 363 416 558 OA21x2_ASAP7_75t_R $T=49464 15120 0 0 $X=49464 $Y=15032
X5220 VSS VDD 172 113 21 607 116 482 AO221x1_ASAP7_75t_R $T=13824 30240 0 0 $X=13824 $Y=30010
X5221 VSS VDD 189 172 21 193 180 625 AO221x1_ASAP7_75t_R $T=22464 38880 1 0 $X=22464 $Y=37712
X5222 VSS VDD 172 672 21 507 210 223 AO221x1_ASAP7_75t_R $T=24840 32400 1 0 $X=24840 $Y=31232
X5223 VSS VDD 630 634 323 329 269 302 AO221x1_ASAP7_75t_R $T=33048 49680 0 0 $X=33048 $Y=49450
X5224 VSS VDD 241 634 323 329 254 304 AO221x1_ASAP7_75t_R $T=33048 54000 1 0 $X=33048 $Y=52832
X5225 VSS VDD 254 634 323 329 216 308 AO221x1_ASAP7_75t_R $T=33480 54000 0 0 $X=33480 $Y=53770
X5226 VSS VDD 216 634 43 329 214 309 AO221x1_ASAP7_75t_R $T=33696 56160 1 0 $X=33696 $Y=54992
X5227 VSS VDD 214 634 323 329 276 312 AO221x1_ASAP7_75t_R $T=33912 56160 0 0 $X=33912 $Y=55930
X5228 VSS VDD 657 43 386 40 652 653 AO221x1_ASAP7_75t_R $T=41688 49680 1 0 $X=41688 $Y=48512
X5229 VSS VDD 40 336 420 43 640 559 AO221x1_ASAP7_75t_R $T=49248 41040 0 0 $X=49248 $Y=40810
X5240 VSS VDD 104 82 21 611 137 OR4x1_ASAP7_75t_R $T=16848 17280 0 0 $X=16848 $Y=17192
X5241 VSS VDD 104 156 82 21 142 OR4x1_ASAP7_75t_R $T=18144 17280 1 0 $X=18144 $Y=16112
X5242 VSS VDD 198 174 171 491 82 OR4x1_ASAP7_75t_R $T=19008 28080 0 0 $X=19008 $Y=27992
X5243 VSS VDD 30 104 178 207 492 OR4x1_ASAP7_75t_R $T=21384 49680 0 0 $X=21384 $Y=49592
X5244 VSS VDD 30 568 104 183 495 OR4x1_ASAP7_75t_R $T=23544 51840 0 0 $X=23544 $Y=51752
X5245 VSS VDD 30 31 104 34 215 OR4x1_ASAP7_75t_R $T=26352 45360 0 0 $X=26352 $Y=45272
X5246 VSS VDD 30 218 104 225 226 OR4x1_ASAP7_75t_R $T=26784 51840 1 0 $X=26784 $Y=50672
X5247 VSS VDD 314 291 279 275 272 OR4x1_ASAP7_75t_R $T=33480 28080 0 0 $X=33480 $Y=27992
X5248 VSS VDD 321 530 327 328 577 OR4x1_ASAP7_75t_R $T=37152 30240 0 0 $X=37152 $Y=30152
X5249 VSS VDD 529 550 373 414 641 OR4x1_ASAP7_75t_R $T=47088 38880 0 0 $X=47088 $Y=38792
X5250 VSS VDD 551 405 550 401 43 OR4x1_ASAP7_75t_R $T=48600 38880 0 0 $X=48600 $Y=38792
X5251 VSS VDD 551 405 550 401 323 OR4x1_ASAP7_75t_R $T=48600 41040 1 0 $X=48600 $Y=39872
X5252 VSS VDD 577 43 21 464 413 OR4x1_ASAP7_75t_R $T=49032 30240 0 0 $X=49032 $Y=30152
X5253 VSS VDD 58 94 104 103 OAI21x1_ASAP7_75t_R $T=8856 21600 0 0 $X=8856 $Y=21446
X5254 VSS VDD 152 144 116 121 OAI21x1_ASAP7_75t_R $T=14904 30240 1 0 $X=14904 $Y=29072
X5255 VSS VDD 133 82 138 130 OAI21x1_ASAP7_75t_R $T=16200 38880 1 0 $X=16200 $Y=37712
X5256 VSS VDD 255 291 288 299 OAI21x1_ASAP7_75t_R $T=33264 45360 1 0 $X=33264 $Y=44192
X5257 VSS VDD 529 292 288 316 OAI21x1_ASAP7_75t_R $T=35424 45360 1 0 $X=35424 $Y=44192
X5258 VSS VDD 292 341 85 600 OAI21x1_ASAP7_75t_R $T=36504 43200 0 0 $X=36504 $Y=43046
X5259 VSS VDD 529 321 635 314 OAI21x1_ASAP7_75t_R $T=36936 28080 0 0 $X=36936 $Y=27926
X5260 VSS VDD 325 292 85 336 OAI21x1_ASAP7_75t_R $T=37584 43200 1 0 $X=37584 $Y=42032
X5261 VSS VDD 363 328 329 364 OAI21x1_ASAP7_75t_R $T=41472 19440 0 0 $X=41472 $Y=19286
X5262 VSS VDD 363 367 329 573 OAI21x1_ASAP7_75t_R $T=41688 17280 0 0 $X=41688 $Y=17126
X5263 VSS VDD 363 574 329 604 OAI21x1_ASAP7_75t_R $T=44064 15120 0 0 $X=44064 $Y=14966
X5264 VSS VDD 363 396 329 687 OAI21x1_ASAP7_75t_R $T=46224 15120 0 0 $X=46224 $Y=14966
X5265 VSS VDD 363 424 329 547 OAI21x1_ASAP7_75t_R $T=46872 21600 1 0 $X=46872 $Y=20432
X5266 VSS VDD 363 427 329 661 OAI21x1_ASAP7_75t_R $T=48600 21600 1 0 $X=48600 $Y=20432
X5267 VSS VDD 363 425 329 417 OAI21x1_ASAP7_75t_R $T=48816 21600 0 0 $X=48816 $Y=21446
X5268 VSS VDD 363 439 329 416 OAI21x1_ASAP7_75t_R $T=49032 17280 1 0 $X=49032 $Y=16112
X5282 VSS VDD 477 104 93 NOR2x1_ASAP7_75t_R $T=10152 38880 0 0 $X=10152 $Y=38792
X5283 VSS VDD 104 480 621 NOR2x1_ASAP7_75t_R $T=12744 47520 0 0 $X=12744 $Y=47432
X5284 VSS VDD 622 156 486 NOR2x1_ASAP7_75t_R $T=17280 10800 0 0 $X=17280 $Y=10712
X5285 VSS VDD 4 487 161 NOR2x1_ASAP7_75t_R $T=18360 45360 1 0 $X=18360 $Y=44192
X5286 VSS VDD 104 160 132 NOR2x1_ASAP7_75t_R $T=18792 47520 0 0 $X=18792 $Y=47432
X5287 VSS VDD 217 196 168 NOR2x1_ASAP7_75t_R $T=19224 15120 1 0 $X=19224 $Y=13952
X5288 VSS VDD 164 156 673 NOR2x1_ASAP7_75t_R $T=20088 6480 1 0 $X=20088 $Y=5312
X5289 VSS VDD 58 180 188 NOR2x1_ASAP7_75t_R $T=21600 34560 1 0 $X=21600 $Y=33392
X5290 VSS VDD 182 156 496 NOR2x1_ASAP7_75t_R $T=22896 8640 0 0 $X=22896 $Y=8552
X5291 VSS VDD 202 625 194 NOR2x1_ASAP7_75t_R $T=23760 36720 1 0 $X=23760 $Y=35552
X5292 VSS VDD 501 156 650 NOR2x1_ASAP7_75t_R $T=26568 8640 0 0 $X=26568 $Y=8552
X5293 VSS VDD 222 223 676 NOR2x1_ASAP7_75t_R $T=27000 30240 1 0 $X=27000 $Y=29072
X5294 VSS VDD 245 156 629 NOR2x1_ASAP7_75t_R $T=30672 6480 0 0 $X=30672 $Y=6392
X5295 VSS VDD 267 156 628 NOR2x1_ASAP7_75t_R $T=31752 15120 1 0 $X=31752 $Y=13952
X5296 VSS VDD 283 156 525 NOR2x1_ASAP7_75t_R $T=32184 10800 0 0 $X=32184 $Y=10712
X5297 VSS VDD 289 156 290 NOR2x1_ASAP7_75t_R $T=34776 8640 1 0 $X=34776 $Y=7472
X5298 VSS VDD 21 65 332 NOR2x1_ASAP7_75t_R $T=37368 36720 1 0 $X=37368 $Y=35552
X5299 VSS VDD 300 321 329 NOR2x1_ASAP7_75t_R $T=38664 28080 0 0 $X=38664 $Y=27992
X5300 VSS VDD 328 300 353 NOR2x1_ASAP7_75t_R $T=39528 28080 1 0 $X=39528 $Y=26912
X5301 VSS VDD 21 361 371 NOR2x1_ASAP7_75t_R $T=42336 32400 0 0 $X=42336 $Y=32312
X5302 VSS VDD 375 407 386 NOR2x1_ASAP7_75t_R $T=43848 49680 1 0 $X=43848 $Y=48512
X5303 VSS VDD 21 379 544 NOR2x1_ASAP7_75t_R $T=44928 38880 0 0 $X=44928 $Y=38792
X5304 VSS VDD 405 577 426 NOR2x1_ASAP7_75t_R $T=48600 32400 0 0 $X=48600 $Y=32312
X5374 VSS VDD 88 82 475 89 ICV_57 $T=7776 30240 1 0 $X=7776 $Y=29072
X5375 VSS VDD 126 318 292 524 ICV_57 $T=14472 38880 0 0 $X=14472 $Y=38670
X5376 VSS VDD 494 58 186 499 ICV_57 $T=23112 21600 0 0 $X=23112 $Y=21390
X5377 VSS VDD 342 534 312 317 ICV_57 $T=35640 58320 1 0 $X=35640 $Y=57152
X5378 VSS VDD 285 297 313 333 ICV_57 $T=36288 23760 0 0 $X=36288 $Y=23550
X5379 VSS VDD 362 376 384 541 ICV_57 $T=43416 45360 1 0 $X=43416 $Y=44192
X5380 VSS VDD 538 393 407 381 ICV_57 $T=44280 54000 0 0 $X=44280 $Y=53790
X5381 VSS VDD 375 408 421 393 ICV_57 $T=48168 54000 0 0 $X=48168 $Y=53790
X5382 VSS VDD 619 88 86 478 22 AND4x1_ASAP7_75t_R $T=9072 32400 1 0 $X=9072 $Y=31232
X5383 VSS VDD 98 129 481 22 117 AND4x1_ASAP7_75t_R $T=12528 28080 1 0 $X=12528 $Y=26912
X5384 VSS VDD 7 6 82 133 141 AND4x1_ASAP7_75t_R $T=15984 38880 0 0 $X=15984 $Y=38730
X5385 VSS VDD 98 129 22 17 131 AND4x1_ASAP7_75t_R $T=16632 30240 1 0 $X=16632 $Y=29072
X5386 VSS VDD 7 6 5 131 487 AND4x1_ASAP7_75t_R $T=17496 43200 1 0 $X=17496 $Y=42032
X5387 VSS VDD 24 159 15 9 144 AND4x1_ASAP7_75t_R $T=20520 30240 0 0 $X=20520 $Y=30090
X5388 VSS VDD 494 243 165 58 197 AND4x1_ASAP7_75t_R $T=22464 21600 1 0 $X=22464 $Y=20432
X5389 VSS VDD 66 209 204 220 570 AND4x1_ASAP7_75t_R $T=24840 28080 0 0 $X=24840 $Y=27930
X5390 VSS VDD 66 231 571 239 244 AND4x1_ASAP7_75t_R $T=27648 41040 0 0 $X=27648 $Y=40890
X5391 VSS VDD 229 242 510 280 9 AND4x1_ASAP7_75t_R $T=29376 34560 1 0 $X=29376 $Y=33392
X5392 VSS VDD 44 354 529 335 363 AND4x1_ASAP7_75t_R $T=41256 25920 1 0 $X=41256 $Y=24752
X5393 VSS VDD 328 66 529 350 368 AND4x1_ASAP7_75t_R $T=41904 30240 1 0 $X=41904 $Y=29072
X5394 VSS VDD 538 369 365 349 384 AND4x1_ASAP7_75t_R $T=42768 51840 0 0 $X=42768 $Y=51690
X5395 VSS VDD 359 348 385 537 379 AND4x1_ASAP7_75t_R $T=42984 38880 1 0 $X=42984 $Y=37712
X5396 VSS VDD 372 359 348 383 402 AND4x1_ASAP7_75t_R $T=43416 36720 0 0 $X=43416 $Y=36570
X5397 VSS VDD 376 384 388 640 655 AND4x1_ASAP7_75t_R $T=44496 47520 1 0 $X=44496 $Y=46352
X5398 VSS VDD 576 402 541 388 529 AND4x1_ASAP7_75t_R $T=46224 43200 0 0 $X=46224 $Y=43050
X5399 VSS VDD 414 456 443 553 400 AND4x1_ASAP7_75t_R $T=47736 49680 0 0 $X=47736 $Y=49530
X5400 VSS VDD 402 400 393 419 554 AND4x1_ASAP7_75t_R $T=48168 43200 0 0 $X=48168 $Y=43050
X5401 VSS VDD 414 456 443 552 578 AND4x1_ASAP7_75t_R $T=48168 47520 1 0 $X=48168 $Y=46352
X5402 VSS VDD 10 105 108 144 76 OAI22x1_ASAP7_75t_R $T=10152 36720 0 0 $X=10152 $Y=36534
X5403 VSS VDD 341 306 123 292 322 OAI22x1_ASAP7_75t_R $T=35424 41040 0 0 $X=35424 $Y=40854
X5404 VSS VDD 314 340 338 331 347 OAI22x1_ASAP7_75t_R $T=38232 23760 1 0 $X=38232 $Y=22592
X5405 VSS VDD 577 323 556 464 428 OAI22x1_ASAP7_75t_R $T=48168 32400 1 0 $X=48168 $Y=31232
X5451 VSS VDD 126 87 100 266 HAxp5_ASAP7_75t_R $T=8424 43200 1 0 $X=8424 $Y=42032
X5452 VSS VDD 98 129 90 102 HAxp5_ASAP7_75t_R $T=9288 25920 1 0 $X=9288 $Y=24752
X5453 VSS VDD 98 129 143 688 HAxp5_ASAP7_75t_R $T=15984 25920 0 0 $X=15984 $Y=25832
X5454 VSS VDD 569 217 497 648 HAxp5_ASAP7_75t_R $T=21600 17280 0 0 $X=21600 $Y=17192
X5455 VSS VDD 192 217 500 689 HAxp5_ASAP7_75t_R $T=23544 15120 0 0 $X=23544 $Y=15032
X5456 VSS VDD 285 297 300 690 HAxp5_ASAP7_75t_R $T=34344 23760 0 0 $X=34344 $Y=23672
X5457 VSS VDD 285 297 279 273 HAxp5_ASAP7_75t_R $T=34344 25920 0 0 $X=34344 $Y=25832
X5458 VSS VDD 265 297 530 691 HAxp5_ASAP7_75t_R $T=35640 25920 1 0 $X=35640 $Y=24752
X5459 VSS VDD 406 399 373 639 HAxp5_ASAP7_75t_R $T=44280 34560 0 0 $X=44280 $Y=34472
X5460 VSS VDD 406 399 401 692 HAxp5_ASAP7_75t_R $T=46008 36720 1 0 $X=46008 $Y=35552
X5478 VSS VDD 23 26 29 491 NAND3x1_ASAP7_75t_R $T=19224 34560 1 0 $X=19224 $Y=33392
X5479 VSS VDD 378 323 376 658 NAND3x1_ASAP7_75t_R $T=44064 58320 1 0 $X=44064 $Y=57152
X5480 VSS VDD 384 376 362 405 NAND3x1_ASAP7_75t_R $T=44928 45360 1 0 $X=44928 $Y=44192
X5512 VSS VDD 229 242 510 24 131 512 AND5x1_ASAP7_75t_R $T=27648 34560 1 0 $X=27648 $Y=33392
X5513 VSS VDD 538 369 365 388 640 681 AND5x1_ASAP7_75t_R $T=44280 51840 0 0 $X=44280 $Y=51630
X5514 VSS VDD 176 8 BUFx4_ASAP7_75t_R $T=17712 38880 0 0 $X=17712 $Y=38792
X5515 VSS VDD 176 177 BUFx4_ASAP7_75t_R $T=21600 23760 0 0 $X=21600 $Y=23672
X5516 VSS VDD 176 201 BUFx4_ASAP7_75t_R $T=22896 17280 1 0 $X=22896 $Y=16112
X5517 VSS VDD 176 32 BUFx4_ASAP7_75t_R $T=29592 38880 1 0 $X=29592 $Y=37712
X5518 VSS VDD clk 176 BUFx4_ASAP7_75t_R $T=30672 32400 1 0 $X=30672 $Y=31232
X5519 VSS VDD 176 360 BUFx4_ASAP7_75t_R $T=41256 17280 1 0 $X=41256 $Y=16112
X5520 VSS VDD 176 46 BUFx4_ASAP7_75t_R $T=42768 47520 1 0 $X=42768 $Y=46352
X5521 VSS VDD 176 370 BUFx4_ASAP7_75t_R $T=43416 23760 1 0 $X=43416 $Y=22592
X5522 VSS VDD 176 48 BUFx4_ASAP7_75t_R $T=44280 41040 1 0 $X=44280 $Y=39872
X5533 VSS VDD 177 INVx3_ASAP7_75t_R $T=21600 25920 1 0 $X=21600 $Y=24752
X5534 VSS VDD 201 INVx3_ASAP7_75t_R $T=23976 19440 1 0 $X=23976 $Y=18272
X5535 VSS VDD 370 INVx3_ASAP7_75t_R $T=43416 23760 0 0 $X=43416 $Y=23672
X5550 VSS VDD 81 75 85 XOR2x2_ASAP7_75t_R $T=6264 45360 1 0 $X=6264 $Y=44192
X5551 VSS VDD 90 619 95 XOR2x2_ASAP7_75t_R $T=7560 23760 1 0 $X=7560 $Y=22592
X5552 VSS VDD 101 583 105 XOR2x2_ASAP7_75t_R $T=8856 45360 1 0 $X=8856 $Y=44192
X5553 VSS VDD 106 478 108 XOR2x2_ASAP7_75t_R $T=9288 34560 0 0 $X=9288 $Y=34306
X5554 VSS VDD 110 473 113 XOR2x2_ASAP7_75t_R $T=10152 41040 0 0 $X=10152 $Y=40786
X5555 VSS VDD 114 473 123 XOR2x2_ASAP7_75t_R $T=12096 43200 1 0 $X=12096 $Y=42032
X5556 VSS VDD 187 183 189 XOR2x2_ASAP7_75t_R $T=20952 45360 1 0 $X=20952 $Y=44192
X5557 VSS VDD 495 589 672 XOR2x2_ASAP7_75t_R $T=23760 54000 1 0 $X=23760 $Y=52832
X5558 VSS VDD 373 372 654 XOR2x2_ASAP7_75t_R $T=41904 41040 1 0 $X=41904 $Y=39872
X5559 VSS VDD 381 369 599 XOR2x2_ASAP7_75t_R $T=42768 56160 0 0 $X=42768 $Y=55906
X5560 VSS VDD 94 129 104 82 620 AOI211x1_ASAP7_75t_R $T=9288 23760 0 0 $X=9288 $Y=23672
X5561 VSS VDD 94 98 104 82 112 AOI211x1_ASAP7_75t_R $T=9720 19440 0 0 $X=9720 $Y=19352
X5562 VSS VDD 351 399 323 341 387 AOI211x1_ASAP7_75t_R $T=42768 34560 1 0 $X=42768 $Y=33392
X5563 VSS VDD 100 473 583 81 OR3x1_ASAP7_75t_R $T=7128 45360 0 0 $X=7128 $Y=45166
X5564 VSS VDD 77 583 84 114 OR3x1_ASAP7_75t_R $T=9504 43200 0 0 $X=9504 $Y=43006
X5565 VSS VDD 21 82 150 120 OR3x1_ASAP7_75t_R $T=14472 15120 0 0 $X=14472 $Y=14926
X5566 VSS VDD 119 252 480 30 OR3x1_ASAP7_75t_R $T=14904 47520 0 0 $X=14904 $Y=47326
X5567 VSS VDD 132 10 566 645 OR3x1_ASAP7_75t_R $T=16200 47520 0 0 $X=16200 $Y=47326
X5568 VSS VDD 5 149 130 153 OR3x1_ASAP7_75t_R $T=17496 41040 1 0 $X=17496 $Y=39872
X5569 VSS VDD 21 567 148 158 OR3x1_ASAP7_75t_R $T=18144 21600 1 0 $X=18144 $Y=20432
X5570 VSS VDD 29 609 130 685 OR3x1_ASAP7_75t_R $T=18144 36720 1 0 $X=18144 $Y=35552
X5571 VSS VDD 207 104 160 181 OR3x1_ASAP7_75t_R $T=19440 49680 1 0 $X=19440 $Y=48512
X5572 VSS VDD 135 497 665 138 OR3x1_ASAP7_75t_R $T=21384 19440 1 0 $X=21384 $Y=18272
X5573 VSS VDD 104 160 568 187 OR3x1_ASAP7_75t_R $T=22032 45360 0 0 $X=22032 $Y=45166
X5574 VSS VDD 183 568 30 205 OR3x1_ASAP7_75t_R $T=22896 56160 1 0 $X=22896 $Y=54992
X5575 VSS VDD 207 178 515 568 OR3x1_ASAP7_75t_R $T=23112 54000 0 0 $X=23112 $Y=53806
X5576 VSS VDD 166 198 208 204 OR3x1_ASAP7_75t_R $T=23328 28080 0 0 $X=23328 $Y=27886
X5577 VSS VDD 569 82 156 649 OR3x1_ASAP7_75t_R $T=23544 12960 1 0 $X=23544 $Y=11792
X5578 VSS VDD 252 118 568 626 OR3x1_ASAP7_75t_R $T=23976 51840 1 0 $X=23976 $Y=50672
X5579 VSS VDD 82 156 503 505 OR3x1_ASAP7_75t_R $T=25488 15120 0 0 $X=25488 $Y=14926
X5580 VSS VDD 21 104 624 173 OR3x1_ASAP7_75t_R $T=25488 19440 1 0 $X=25488 $Y=18272
X5581 VSS VDD 252 207 118 219 OR3x1_ASAP7_75t_R $T=25488 51840 1 0 $X=25488 $Y=50672
X5582 VSS VDD 252 118 218 230 OR3x1_ASAP7_75t_R $T=25920 49680 0 0 $X=25920 $Y=49486
X5583 VSS VDD 104 160 218 509 OR3x1_ASAP7_75t_R $T=26784 47520 0 0 $X=26784 $Y=47326
X5584 VSS VDD 207 178 30 236 OR3x1_ASAP7_75t_R $T=27000 54000 0 0 $X=27000 $Y=53806
X5585 VSS VDD 234 144 512 571 OR3x1_ASAP7_75t_R $T=27648 41040 1 0 $X=27648 $Y=39872
X5586 VSS VDD 225 246 218 31 OR3x1_ASAP7_75t_R $T=27648 49680 1 0 $X=27648 $Y=48512
X5587 VSS VDD 225 218 30 511 OR3x1_ASAP7_75t_R $T=27648 54000 1 0 $X=27648 $Y=52832
X5588 VSS VDD 104 160 31 627 OR3x1_ASAP7_75t_R $T=27864 45360 0 0 $X=27864 $Y=45166
X5589 VSS VDD 261 144 262 517 OR3x1_ASAP7_75t_R $T=29376 32400 0 0 $X=29376 $Y=32206
X5590 VSS VDD 265 291 314 667 OR3x1_ASAP7_75t_R $T=33264 25920 1 0 $X=33264 $Y=24752
X5591 VSS VDD 341 329 123 325 OR3x1_ASAP7_75t_R $T=35424 43200 1 0 $X=35424 $Y=42032
X5592 VSS VDD 279 327 343 315 OR3x1_ASAP7_75t_R $T=35856 30240 1 0 $X=35856 $Y=29072
X5593 VSS VDD 328 292 43 575 OR3x1_ASAP7_75t_R $T=40608 30240 1 0 $X=40608 $Y=29072
X5594 VSS VDD 81 104 662 OR2x2_ASAP7_75t_R $T=8640 45360 0 0 $X=8640 $Y=45272
X5595 VSS VDD 104 100 101 OR2x2_ASAP7_75t_R $T=9936 45360 0 0 $X=9936 $Y=45272
X5596 VSS VDD 114 104 110 OR2x2_ASAP7_75t_R $T=10368 43200 1 0 $X=10368 $Y=42032
X5597 VSS VDD 30 104 488 OR2x2_ASAP7_75t_R $T=18576 51840 1 0 $X=18576 $Y=50672
X5598 VSS VDD 118 252 160 OR2x2_ASAP7_75t_R $T=25488 49680 1 0 $X=25488 $Y=48512
X5599 VSS VDD 31 160 253 OR2x2_ASAP7_75t_R $T=28944 49680 1 0 $X=28944 $Y=48512
X5600 VSS VDD 284 21 633 OR2x2_ASAP7_75t_R $T=33912 17280 0 0 $X=33912 $Y=17192
X5601 VSS VDD 321 300 292 OR2x2_ASAP7_75t_R $T=38664 30240 0 0 $X=38664 $Y=30152
X5602 VSS VDD 331 328 320 OR2x2_ASAP7_75t_R $T=39744 25920 0 0 $X=39744 $Y=25832
X5603 VSS VDD 88 82 475 80 AOI21x1_ASAP7_75t_R $T=7560 30240 0 0 $X=7560 $Y=30086
X5604 VSS VDD 482 481 121 677 AOI21x1_ASAP7_75t_R $T=13176 30240 1 0 $X=13176 $Y=29072
X5605 VSS VDD 21 196 124 125 AOI21x1_ASAP7_75t_R $T=14472 36720 1 0 $X=14472 $Y=35552
X5606 VSS VDD 6 7 131 663 AOI21x1_ASAP7_75t_R $T=15552 43200 0 0 $X=15552 $Y=43046
X5607 VSS VDD 140 622 137 483 AOI21x1_ASAP7_75t_R $T=15768 15120 0 0 $X=15768 $Y=14966
X5608 VSS VDD 243 165 144 200 AOI21x1_ASAP7_75t_R $T=23976 21600 1 0 $X=23976 $Y=20432
X5609 VSS VDD 21 667 263 259 AOI21x1_ASAP7_75t_R $T=30672 23760 0 0 $X=30672 $Y=23606
X5610 VSS VDD 21 261 262 523 AOI21x1_ASAP7_75t_R $T=30672 32400 0 0 $X=30672 $Y=32246
X5611 VSS VDD 21 287 272 522 AOI21x1_ASAP7_75t_R $T=31752 28080 0 0 $X=31752 $Y=27926
X5612 VSS VDD 329 331 333 340 AOI21x1_ASAP7_75t_R $T=38016 23760 0 0 $X=38016 $Y=23606
X5613 VSS VDD 320 313 533 341 AOI21x1_ASAP7_75t_R $T=39528 25920 1 0 $X=39528 $Y=24752
X5614 VSS VDD 133 82 23 172 184 684 AO32x1_ASAP7_75t_R $T=19872 38880 1 0 $X=19872 $Y=37712
X5615 VSS VDD 248 82 66 240 235 508 AO32x1_ASAP7_75t_R $T=27864 38880 1 0 $X=27864 $Y=37712
X5616 VSS VDD 241 303 327 249 634 652 AO32x1_ASAP7_75t_R $T=33696 51840 1 0 $X=33696 $Y=50672
X5617 VSS VDD 630 303 327 276 634 686 AO32x1_ASAP7_75t_R $T=34344 51840 0 0 $X=34344 $Y=51706
X5618 VSS VDD 249 303 327 256 634 632 AO32x1_ASAP7_75t_R $T=34560 49680 1 0 $X=34560 $Y=48512
X5619 VSS VDD 256 303 327 319 634 605 AO32x1_ASAP7_75t_R $T=35424 47520 0 0 $X=35424 $Y=47386
X5620 VSS VDD 339 43 66 344 686 616 AO32x1_ASAP7_75t_R $T=38880 51840 0 0 $X=38880 $Y=51706
X5621 VSS VDD 492 515 184 XNOR2x1_ASAP7_75t_R $T=20520 51840 1 0 $X=20520 $Y=50672
X5622 VSS VDD 205 589 214 XNOR2x1_ASAP7_75t_R $T=24408 56160 0 0 $X=24408 $Y=55978
X5623 VSS VDD 236 515 254 XNOR2x1_ASAP7_75t_R $T=28296 54000 0 0 $X=28296 $Y=53818
X5624 VSS VDD 511 246 630 XNOR2x1_ASAP7_75t_R $T=28944 54000 1 0 $X=28944 $Y=52832
X5625 VSS VDD 253 34 269 XNOR2x1_ASAP7_75t_R $T=30240 49680 1 0 $X=30240 $Y=48512
X5626 VSS VDD 75 77 473 583 84 480 OR5x1_ASAP7_75t_R $T=7776 43200 0 0 $X=7776 $Y=42990
X5627 VSS VDD 100 75 119 473 583 118 OR5x1_ASAP7_75t_R $T=11880 45360 0 0 $X=11880 $Y=45150
X5628 VSS VDD 491 171 174 198 94 624 OR5x1_ASAP7_75t_R $T=20520 28080 0 0 $X=20520 $Y=27870
X5629 VSS VDD 207 178 589 515 183 218 OR5x1_ASAP7_75t_R $T=25272 54000 0 0 $X=25272 $Y=53790
X5630 VSS VDD 34 227 160 31 292 255 OR5x1_ASAP7_75t_R $T=28728 45360 1 0 $X=28728 $Y=44192
X5631 VSS VDD ICV_67 $T=14256 56160 0 0 $X=14256 $Y=56072
X5632 VSS VDD ICV_67 $T=14256 58320 0 0 $X=14256 $Y=58232
X5686 VSS VDD ICV_68 $T=4320 4320 0 0 $X=4320 $Y=4232
X5687 VSS VDD ICV_68 $T=4320 6480 0 0 $X=4320 $Y=6392
X5688 VSS VDD ICV_68 $T=4320 8640 0 0 $X=4320 $Y=8552
X5689 VSS VDD ICV_68 $T=4320 10800 0 0 $X=4320 $Y=10712
X5690 VSS VDD ICV_68 $T=4320 49680 0 0 $X=4320 $Y=49592
X5691 VSS VDD ICV_68 $T=4320 51840 0 0 $X=4320 $Y=51752
X5692 VSS VDD ICV_68 $T=4320 54000 0 0 $X=4320 $Y=53912
X5693 VSS VDD ICV_68 $T=4320 56160 0 0 $X=4320 $Y=56072
X5694 VSS VDD ICV_68 $T=4320 58320 0 0 $X=4320 $Y=58232
.ENDS
***************************************
