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
.SUBCKT ICV_7 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=4
X1 1 2 DECAPx2_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
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
.SUBCKT ICV_10
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_11 1 2
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
.SUBCKT ICV_12
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_13 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_14 1 2 3 4
*.CALIBRE ICV_CELL 9
** N=4 EP=4 IP=6 FDC=2
X1 1 2 3 4 INVx1_ASAP7_75t_R $T=216 0 0 0 $X=216 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_15 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=8
X1 1 2 DECAPx4_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_16
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
.SUBCKT ICV_17
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_18 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=12
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_19 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=14
X0 1 2 DECAPx1_ASAP7_75t_R $T=3024 0 0 0 $X=3024 $Y=-88
X1 1 2 DECAPx6_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_20
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
.SUBCKT ICV_21
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
.SUBCKT ICV_22
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_23 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=28
X0 1 2 DECAPx4_ASAP7_75t_R $T=4752 0 0 0 $X=4752 $Y=-88
X1 1 2 DECAPx10_ASAP7_75t_R $T=0 0 0 0 $X=0 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_24
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
.SUBCKT ICV_25 1 2 3 4 5
*.CALIBRE ICV_CELL 9
** N=5 EP=5 IP=7 FDC=24
X1 1 2 3 4 5 DFFHQNx1_ASAP7_75t_R $T=432 0 0 0 $X=432 $Y=-88
.ENDS
***************************************
.SUBCKT ICV_26
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_35
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
.SUBCKT ICV_36 1 2
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
.SUBCKT ICV_37 1 2 3 4 5
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
.SUBCKT ICV_46
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
.SUBCKT ICV_50
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
.SUBCKT ICV_51
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_52
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
.SUBCKT ICV_53
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
.SUBCKT ICV_54 1 2 3 4 5 6
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
.SUBCKT ICV_55
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_56
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_57
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_60
** N=0 EP=0 IP=0 FDC=0
.ENDS
***************************************
.SUBCKT ICV_61
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_64
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
.SUBCKT VIA_VIA67
** N=0 EP=0 IP=0 FDC=0
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
.SUBCKT ICV_66 1 2
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
.SUBCKT ICV_67 1 2
*.CALIBRE ICV_CELL 9
** N=2 EP=2 IP=4 FDC=80
X1 1 2 ICV_66 $T=432 0 0 0 $X=432 $Y=-88
.ENDS
***************************************
.SUBCKT uart VSS VDD prescale[5] prescale[2] tx_busy rst prescale[1] s_axis_tready s_axis_tdata[7] prescale[8] s_axis_tdata[2] rx_busy m_axis_tready m_axis_tdata[4] rx_overrun_error m_axis_tdata[2] m_axis_tdata[1] m_axis_tdata[0] m_axis_tvalid prescale[3]
+ prescale[4] prescale[0] s_axis_tvalid s_axis_tdata[6] s_axis_tdata[5] prescale[7] prescale[11] prescale[10] s_axis_tdata[4] prescale[6] prescale[9] s_axis_tdata[0] clk prescale[12] prescale[13] rxd prescale[14] m_axis_tdata[6] m_axis_tdata[5] rx_frame_error
+ s_axis_tdata[3] prescale[15] s_axis_tdata[1] m_axis_tdata[3] txd m_axis_tdata[7]
** N=764 EP=46 IP=4570 FDC=12999
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
X3172 VSS VDD DECAPx1_ASAP7_75t_R $T=6912 43200 0 0 $X=6912 $Y=43112
X3173 VSS VDD DECAPx1_ASAP7_75t_R $T=9504 17280 0 0 $X=9504 $Y=17192
X3174 VSS VDD DECAPx1_ASAP7_75t_R $T=9504 19440 1 0 $X=9504 $Y=18272
X3175 VSS VDD DECAPx1_ASAP7_75t_R $T=9504 38880 1 0 $X=9504 $Y=37712
X3176 VSS VDD DECAPx1_ASAP7_75t_R $T=14040 47520 0 0 $X=14040 $Y=47432
X3177 VSS VDD DECAPx1_ASAP7_75t_R $T=16632 41040 1 0 $X=16632 $Y=39872
X3178 VSS VDD DECAPx1_ASAP7_75t_R $T=17064 19440 0 0 $X=17064 $Y=19352
X3179 VSS VDD DECAPx1_ASAP7_75t_R $T=19224 19440 0 0 $X=19224 $Y=19352
X3180 VSS VDD DECAPx1_ASAP7_75t_R $T=20736 23760 0 0 $X=20736 $Y=23672
X3181 VSS VDD DECAPx1_ASAP7_75t_R $T=20736 25920 1 0 $X=20736 $Y=24752
X3182 VSS VDD DECAPx1_ASAP7_75t_R $T=22032 56160 1 0 $X=22032 $Y=54992
X3183 VSS VDD DECAPx1_ASAP7_75t_R $T=22464 6480 1 0 $X=22464 $Y=5312
X3184 VSS VDD DECAPx1_ASAP7_75t_R $T=22680 12960 1 0 $X=22680 $Y=11792
X3185 VSS VDD DECAPx1_ASAP7_75t_R $T=23112 51840 1 0 $X=23112 $Y=50672
X3186 VSS VDD DECAPx1_ASAP7_75t_R $T=24408 54000 0 0 $X=24408 $Y=53912
X3187 VSS VDD DECAPx1_ASAP7_75t_R $T=26784 49680 1 0 $X=26784 $Y=48512
X3188 VSS VDD DECAPx1_ASAP7_75t_R $T=31536 8640 1 0 $X=31536 $Y=7472
X3189 VSS VDD DECAPx1_ASAP7_75t_R $T=32616 41040 1 0 $X=32616 $Y=39872
X3190 VSS VDD DECAPx1_ASAP7_75t_R $T=35208 49680 0 0 $X=35208 $Y=49592
X3191 VSS VDD DECAPx1_ASAP7_75t_R $T=36720 43200 1 0 $X=36720 $Y=42032
X3192 VSS VDD DECAPx1_ASAP7_75t_R $T=40824 8640 1 0 $X=40824 $Y=7472
X3193 VSS VDD DECAPx1_ASAP7_75t_R $T=42552 23760 1 0 $X=42552 $Y=22592
X3194 VSS VDD DECAPx1_ASAP7_75t_R $T=43848 56160 1 0 $X=43848 $Y=54992
X3195 VSS VDD DECAPx1_ASAP7_75t_R $T=46224 34560 0 0 $X=46224 $Y=34472
X3196 VSS VDD DECAPx1_ASAP7_75t_R $T=46224 38880 0 0 $X=46224 $Y=38792
X3197 VSS VDD DECAPx1_ASAP7_75t_R $T=46440 49680 1 0 $X=46440 $Y=48512
X3198 VSS VDD DECAPx1_ASAP7_75t_R $T=47304 47520 1 0 $X=47304 $Y=46352
X3199 VSS VDD DECAPx1_ASAP7_75t_R $T=47520 25920 1 0 $X=47520 $Y=24752
X3200 VSS VDD DECAPx1_ASAP7_75t_R $T=50544 23760 0 0 $X=50544 $Y=23672
X3201 VSS VDD DECAPx1_ASAP7_75t_R $T=53136 23760 0 0 $X=53136 $Y=23672
X3202 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 12960 1 0 $X=59184 $Y=11792
X3203 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 34560 0 0 $X=59184 $Y=34472
X3204 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 54000 0 0 $X=59184 $Y=53912
X3205 VSS VDD DECAPx1_ASAP7_75t_R $T=59184 58320 1 0 $X=59184 $Y=57152
X3206 VSS VDD ICV_6 $T=4752 43200 0 0 $X=4752 $Y=43112
X3207 VSS VDD ICV_6 $T=7776 38880 0 0 $X=7776 $Y=38792
X3208 VSS VDD ICV_6 $T=11448 28080 1 0 $X=11448 $Y=26912
X3209 VSS VDD ICV_6 $T=14256 49680 1 0 $X=14256 $Y=48512
X3210 VSS VDD ICV_6 $T=17280 43200 0 0 $X=17280 $Y=43112
X3211 VSS VDD ICV_6 $T=17280 45360 1 0 $X=17280 $Y=44192
X3212 VSS VDD ICV_6 $T=17280 49680 0 0 $X=17280 $Y=49592
X3213 VSS VDD ICV_6 $T=17712 19440 1 0 $X=17712 $Y=18272
X3214 VSS VDD ICV_6 $T=18360 12960 0 0 $X=18360 $Y=12872
X3215 VSS VDD ICV_6 $T=19008 6480 1 0 $X=19008 $Y=5312
X3216 VSS VDD ICV_6 $T=21816 8640 0 0 $X=21816 $Y=8552
X3217 VSS VDD ICV_6 $T=22032 54000 0 0 $X=22032 $Y=53912
X3218 VSS VDD ICV_6 $T=24192 8640 0 0 $X=24192 $Y=8552
X3219 VSS VDD ICV_6 $T=24840 12960 1 0 $X=24840 $Y=11792
X3220 VSS VDD ICV_6 $T=28728 23760 1 0 $X=28728 $Y=22592
X3221 VSS VDD ICV_6 $T=30456 25920 1 0 $X=30456 $Y=24752
X3222 VSS VDD ICV_6 $T=30456 58320 1 0 $X=30456 $Y=57152
X3223 VSS VDD ICV_6 $T=30672 28080 0 0 $X=30672 $Y=27992
X3224 VSS VDD ICV_6 $T=31320 10800 1 0 $X=31320 $Y=9632
X3225 VSS VDD ICV_6 $T=31752 30240 0 0 $X=31752 $Y=30152
X3226 VSS VDD ICV_6 $T=32184 32400 1 0 $X=32184 $Y=31232
X3227 VSS VDD ICV_6 $T=35424 43200 0 0 $X=35424 $Y=43112
X3228 VSS VDD ICV_6 $T=43200 34560 0 0 $X=43200 $Y=34472
X3229 VSS VDD ICV_6 $T=45792 21600 1 0 $X=45792 $Y=20432
X3230 VSS VDD ICV_6 $T=45792 54000 0 0 $X=45792 $Y=53912
X3231 VSS VDD ICV_6 $T=46656 49680 0 0 $X=46656 $Y=49592
X3232 VSS VDD ICV_6 $T=47088 43200 1 0 $X=47088 $Y=42032
X3233 VSS VDD ICV_6 $T=49464 43200 1 0 $X=49464 $Y=42032
X3234 VSS VDD ICV_6 $T=50112 19440 0 0 $X=50112 $Y=19352
X3235 VSS VDD ICV_6 $T=50760 17280 1 0 $X=50760 $Y=16112
X3236 VSS VDD ICV_6 $T=58968 10800 0 0 $X=58968 $Y=10712
X3237 VSS VDD ICV_6 $T=58968 15120 1 0 $X=58968 $Y=13952
X3238 VSS VDD ICV_6 $T=58968 32400 1 0 $X=58968 $Y=31232
X3239 VSS VDD ICV_6 $T=58968 32400 0 0 $X=58968 $Y=32312
X3240 VSS VDD ICV_6 $T=58968 49680 0 0 $X=58968 $Y=49592
X3241 VSS VDD prescale[3] 73 BUFx2_ASAP7_75t_R $T=4752 43200 1 0 $X=4752 $Y=42032
X3242 VSS VDD prescale[5] 75 BUFx2_ASAP7_75t_R $T=5184 45360 1 0 $X=5184 $Y=44192
X3243 VSS VDD 70 tx_busy BUFx2_ASAP7_75t_R $T=5400 21600 0 0 $X=5400 $Y=21512
X3244 VSS VDD prescale[4] 79 BUFx2_ASAP7_75t_R $T=5832 43200 0 0 $X=5832 $Y=43112
X3245 VSS VDD prescale[2] 81 BUFx2_ASAP7_75t_R $T=6048 41040 0 0 $X=6048 $Y=40952
X3246 VSS VDD prescale[0] 82 BUFx2_ASAP7_75t_R $T=6048 43200 1 0 $X=6048 $Y=42032
X3247 VSS VDD rst 21 BUFx2_ASAP7_75t_R $T=7128 25920 0 0 $X=7128 $Y=25832
X3248 VSS VDD prescale[1] 88 BUFx2_ASAP7_75t_R $T=7128 43200 1 0 $X=7128 $Y=42032
X3249 VSS VDD s_axis_tvalid 163 BUFx2_ASAP7_75t_R $T=15984 6480 0 0 $X=15984 $Y=6392
X3250 VSS VDD 602 s_axis_tready BUFx2_ASAP7_75t_R $T=16200 10800 0 0 $X=16200 $Y=10712
X3251 VSS VDD s_axis_tdata[7] 507 BUFx2_ASAP7_75t_R $T=18792 4320 0 0 $X=18792 $Y=4232
X3252 VSS VDD s_axis_tdata[6] 196 BUFx2_ASAP7_75t_R $T=21384 6480 1 0 $X=21384 $Y=5312
X3253 VSS VDD s_axis_tdata[5] 521 BUFx2_ASAP7_75t_R $T=22896 4320 0 0 $X=22896 $Y=4232
X3254 VSS VDD prescale[7] 174 BUFx2_ASAP7_75t_R $T=23760 58320 1 0 $X=23760 $Y=57152
X3255 VSS VDD prescale[11] 217 BUFx2_ASAP7_75t_R $T=25056 60480 1 0 $X=25056 $Y=59312
X3256 VSS VDD prescale[10] 197 BUFx2_ASAP7_75t_R $T=25488 58320 1 0 $X=25488 $Y=57152
X3257 VSS VDD s_axis_tdata[4] 536 BUFx2_ASAP7_75t_R $T=26568 4320 0 0 $X=26568 $Y=4232
X3258 VSS VDD 542 txd BUFx2_ASAP7_75t_R $T=28080 8640 0 0 $X=28080 $Y=8552
X3259 VSS VDD prescale[6] 259 BUFx2_ASAP7_75t_R $T=28944 60480 1 0 $X=28944 $Y=59312
X3260 VSS VDD prescale[9] 530 BUFx2_ASAP7_75t_R $T=29160 56160 1 0 $X=29160 $Y=54992
X3261 VSS VDD prescale[8] 192 BUFx2_ASAP7_75t_R $T=29376 58320 1 0 $X=29376 $Y=57152
X3262 VSS VDD s_axis_tdata[0] 546 BUFx2_ASAP7_75t_R $T=30024 4320 0 0 $X=30024 $Y=4232
X3263 VSS VDD s_axis_tdata[3] 550 BUFx2_ASAP7_75t_R $T=30888 8640 0 0 $X=30888 $Y=8552
X3264 VSS VDD prescale[12] 278 BUFx2_ASAP7_75t_R $T=31320 56160 0 0 $X=31320 $Y=56072
X3265 VSS VDD prescale[15] 232 BUFx2_ASAP7_75t_R $T=31536 58320 1 0 $X=31536 $Y=57152
X3266 VSS VDD prescale[13] 280 BUFx2_ASAP7_75t_R $T=31536 58320 0 0 $X=31536 $Y=58232
X3267 VSS VDD s_axis_tdata[1] 556 BUFx2_ASAP7_75t_R $T=31752 4320 0 0 $X=31752 $Y=4232
X3268 VSS VDD s_axis_tdata[2] 287 BUFx2_ASAP7_75t_R $T=32400 8640 1 0 $X=32400 $Y=7472
X3269 VSS VDD rxd 292 BUFx2_ASAP7_75t_R $T=32400 17280 1 0 $X=32400 $Y=16112
X3270 VSS VDD prescale[14] 34 BUFx2_ASAP7_75t_R $T=33048 58320 0 0 $X=33048 $Y=58232
X3271 VSS VDD 567 m_axis_tdata[6] BUFx2_ASAP7_75t_R $T=41688 8640 1 0 $X=41688 $Y=7472
X3272 VSS VDD 580 m_axis_tdata[5] BUFx2_ASAP7_75t_R $T=46440 6480 1 0 $X=46440 $Y=5312
X3273 VSS VDD 413 rx_busy BUFx2_ASAP7_75t_R $T=52488 25920 0 0 $X=52488 $Y=25832
X3274 VSS VDD 469 m_axis_tdata[4] BUFx2_ASAP7_75t_R $T=56160 12960 1 0 $X=56160 $Y=11792
X3275 VSS VDD m_axis_tready 590 BUFx2_ASAP7_75t_R $T=56160 30240 0 0 $X=56160 $Y=30152
X3276 VSS VDD 475 rx_overrun_error BUFx2_ASAP7_75t_R $T=56808 30240 1 0 $X=56808 $Y=29072
X3277 VSS VDD 477 m_axis_tdata[7] BUFx2_ASAP7_75t_R $T=57240 21600 0 0 $X=57240 $Y=21512
X3278 VSS VDD 470 m_axis_tdata[2] BUFx2_ASAP7_75t_R $T=57456 23760 0 0 $X=57456 $Y=23672
X3279 VSS VDD 473 m_axis_tdata[1] BUFx2_ASAP7_75t_R $T=57672 17280 0 0 $X=57672 $Y=17192
X3280 VSS VDD 479 m_axis_tdata[0] BUFx2_ASAP7_75t_R $T=58104 12960 1 0 $X=58104 $Y=11792
X3281 VSS VDD 484 m_axis_tdata[3] BUFx2_ASAP7_75t_R $T=58536 23760 0 0 $X=58536 $Y=23672
X3282 VSS VDD 481 m_axis_tvalid BUFx2_ASAP7_75t_R $T=58536 30240 0 0 $X=58536 $Y=30152
X3283 VSS VDD 474 rx_frame_error BUFx2_ASAP7_75t_R $T=58968 28080 0 0 $X=58968 $Y=27992
X3284 VSS VDD DECAPx2_ASAP7_75t_R $T=4752 41040 0 0 $X=4752 $Y=40952
X3285 VSS VDD DECAPx2_ASAP7_75t_R $T=8856 41040 1 0 $X=8856 $Y=39872
X3286 VSS VDD DECAPx2_ASAP7_75t_R $T=10584 30240 1 0 $X=10584 $Y=29072
X3287 VSS VDD DECAPx2_ASAP7_75t_R $T=10800 25920 0 0 $X=10800 $Y=25832
X3288 VSS VDD DECAPx2_ASAP7_75t_R $T=12528 15120 0 0 $X=12528 $Y=15032
X3289 VSS VDD DECAPx2_ASAP7_75t_R $T=12960 23760 1 0 $X=12960 $Y=22592
X3290 VSS VDD DECAPx2_ASAP7_75t_R $T=13824 41040 1 0 $X=13824 $Y=39872
X3291 VSS VDD DECAPx2_ASAP7_75t_R $T=14472 43200 1 0 $X=14472 $Y=42032
X3292 VSS VDD DECAPx2_ASAP7_75t_R $T=17280 51840 1 0 $X=17280 $Y=50672
X3293 VSS VDD DECAPx2_ASAP7_75t_R $T=17496 47520 0 0 $X=17496 $Y=47432
X3294 VSS VDD DECAPx2_ASAP7_75t_R $T=18792 25920 0 0 $X=18792 $Y=25832
X3295 VSS VDD DECAPx2_ASAP7_75t_R $T=19656 45360 1 0 $X=19656 $Y=44192
X3296 VSS VDD DECAPx2_ASAP7_75t_R $T=20952 10800 1 0 $X=20952 $Y=9632
X3297 VSS VDD DECAPx2_ASAP7_75t_R $T=23760 60480 1 0 $X=23760 $Y=59312
X3298 VSS VDD DECAPx2_ASAP7_75t_R $T=25056 47520 1 0 $X=25056 $Y=46352
X3299 VSS VDD DECAPx2_ASAP7_75t_R $T=26352 10800 0 0 $X=26352 $Y=10712
X3300 VSS VDD DECAPx2_ASAP7_75t_R $T=29160 45360 0 0 $X=29160 $Y=45272
X3301 VSS VDD DECAPx2_ASAP7_75t_R $T=30024 56160 0 0 $X=30024 $Y=56072
X3302 VSS VDD DECAPx2_ASAP7_75t_R $T=35640 28080 0 0 $X=35640 $Y=27992
X3303 VSS VDD DECAPx2_ASAP7_75t_R $T=37584 45360 0 0 $X=37584 $Y=45272
X3304 VSS VDD DECAPx2_ASAP7_75t_R $T=38448 58320 1 0 $X=38448 $Y=57152
X3305 VSS VDD DECAPx2_ASAP7_75t_R $T=38664 19440 0 0 $X=38664 $Y=19352
X3306 VSS VDD DECAPx2_ASAP7_75t_R $T=49680 51840 1 0 $X=49680 $Y=50672
X3307 VSS VDD DECAPx2_ASAP7_75t_R $T=56160 23760 0 0 $X=56160 $Y=23672
X3308 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 6480 0 0 $X=58752 $Y=6392
X3309 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 15120 0 0 $X=58752 $Y=15032
X3310 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 17280 0 0 $X=58752 $Y=17192
X3311 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 36720 1 0 $X=58752 $Y=35552
X3312 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 36720 0 0 $X=58752 $Y=36632
X3313 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 47520 0 0 $X=58752 $Y=47432
X3314 VSS VDD DECAPx2_ASAP7_75t_R $T=58752 49680 1 0 $X=58752 $Y=48512
X3315 VSS VDD ICV_7 $T=4752 32400 0 0 $X=4752 $Y=32312
X3316 VSS VDD ICV_7 $T=7776 25920 1 0 $X=7776 $Y=24752
X3317 VSS VDD ICV_7 $T=12528 12960 0 0 $X=12528 $Y=12872
X3318 VSS VDD ICV_7 $T=14472 25920 0 0 $X=14472 $Y=25832
X3319 VSS VDD ICV_7 $T=14688 38880 1 0 $X=14688 $Y=37712
X3320 VSS VDD ICV_7 $T=17280 4320 0 0 $X=17280 $Y=4232
X3321 VSS VDD ICV_7 $T=19008 30240 0 0 $X=19008 $Y=30152
X3322 VSS VDD ICV_7 $T=19440 21600 1 0 $X=19440 $Y=20432
X3323 VSS VDD ICV_7 $T=22032 51840 0 0 $X=22032 $Y=51752
X3324 VSS VDD ICV_7 $T=25056 51840 0 0 $X=25056 $Y=51752
X3325 VSS VDD ICV_7 $T=26136 54000 1 0 $X=26136 $Y=52832
X3326 VSS VDD ICV_7 $T=31536 54000 1 0 $X=31536 $Y=52832
X3327 VSS VDD ICV_7 $T=32400 56160 0 0 $X=32400 $Y=56072
X3328 VSS VDD ICV_7 $T=35856 47520 1 0 $X=35856 $Y=46352
X3329 VSS VDD ICV_7 $T=37152 51840 1 0 $X=37152 $Y=50672
X3330 VSS VDD ICV_7 $T=37800 10800 0 0 $X=37800 $Y=10712
X3331 VSS VDD ICV_7 $T=39744 12960 1 0 $X=39744 $Y=11792
X3332 VSS VDD ICV_7 $T=40176 8640 0 0 $X=40176 $Y=8552
X3333 VSS VDD ICV_7 $T=40392 41040 1 0 $X=40392 $Y=39872
X3334 VSS VDD ICV_7 $T=42336 41040 0 0 $X=42336 $Y=40952
X3335 VSS VDD ICV_7 $T=42768 10800 1 0 $X=42768 $Y=9632
X3336 VSS VDD ICV_7 $T=42984 12960 1 0 $X=42984 $Y=11792
X3337 VSS VDD ICV_7 $T=44928 6480 1 0 $X=44928 $Y=5312
X3338 VSS VDD ICV_7 $T=45576 54000 1 0 $X=45576 $Y=52832
X3339 VSS VDD ICV_7 $T=46008 56160 1 0 $X=46008 $Y=54992
X3340 VSS VDD ICV_7 $T=47304 12960 0 0 $X=47304 $Y=12872
X3341 VSS VDD ICV_7 $T=47952 15120 0 0 $X=47952 $Y=15032
X3342 VSS VDD ICV_7 $T=50544 21600 0 0 $X=50544 $Y=21512
X3343 VSS VDD ICV_7 $T=58536 45360 0 0 $X=58536 $Y=45272
X3344 VSS VDD ICV_7 $T=58536 60480 1 0 $X=58536 $Y=59312
X3345 VSS VDD ICV_8 $T=4752 36720 1 0 $X=4752 $Y=35552
X3346 VSS VDD ICV_8 $T=6480 21600 0 0 $X=6480 $Y=21512
X3347 VSS VDD ICV_8 $T=14256 6480 0 0 $X=14256 $Y=6392
X3348 VSS VDD ICV_8 $T=14256 12960 1 0 $X=14256 $Y=11792
X3349 VSS VDD ICV_8 $T=18360 17280 0 0 $X=18360 $Y=17192
X3350 VSS VDD ICV_8 $T=29160 8640 0 0 $X=29160 $Y=8552
X3351 VSS VDD ICV_8 $T=30888 25920 0 0 $X=30888 $Y=25832
X3352 VSS VDD ICV_8 $T=34128 30240 0 0 $X=34128 $Y=30152
X3353 VSS VDD ICV_8 $T=34560 28080 1 0 $X=34560 $Y=26912
X3354 VSS VDD ICV_8 $T=35856 56160 1 0 $X=35856 $Y=54992
X3355 VSS VDD ICV_8 $T=38448 19440 1 0 $X=38448 $Y=18272
X3356 VSS VDD ICV_8 $T=39960 17280 0 0 $X=39960 $Y=17192
X3357 VSS VDD ICV_8 $T=44280 36720 1 0 $X=44280 $Y=35552
X3358 VSS VDD ICV_8 $T=47088 21600 0 0 $X=47088 $Y=21512
X3359 VSS VDD ICV_8 $T=58320 21600 0 0 $X=58320 $Y=21512
X3360 VSS VDD ICV_8 $T=58320 23760 1 0 $X=58320 $Y=22592
X3361 VSS VDD ICV_8 $T=58320 25920 0 0 $X=58320 $Y=25832
X3362 VSS VDD ICV_9 $T=8208 25920 0 0 $X=8208 $Y=25832
X3363 VSS VDD ICV_9 $T=8208 36720 0 0 $X=8208 $Y=36632
X3364 VSS VDD ICV_9 $T=12312 36720 0 0 $X=12312 $Y=36632
X3365 VSS VDD ICV_9 $T=14256 10800 0 0 $X=14256 $Y=10712
X3366 VSS VDD ICV_9 $T=18792 47520 1 0 $X=18792 $Y=46352
X3367 VSS VDD ICV_9 $T=19224 36720 0 0 $X=19224 $Y=36632
X3368 VSS VDD ICV_9 $T=19656 17280 1 0 $X=19656 $Y=16112
X3369 VSS VDD ICV_9 $T=24408 34560 1 0 $X=24408 $Y=33392
X3370 VSS VDD ICV_9 $T=31968 8640 0 0 $X=31968 $Y=8552
X3371 VSS VDD ICV_9 $T=36072 56160 0 0 $X=36072 $Y=56072
X3372 VSS VDD ICV_9 $T=39096 15120 1 0 $X=39096 $Y=13952
X3373 VSS VDD ICV_9 $T=40392 49680 0 0 $X=40392 $Y=49592
X3374 VSS VDD ICV_9 $T=40824 47520 0 0 $X=40824 $Y=47432
X3375 VSS VDD ICV_9 $T=41472 23760 0 0 $X=41472 $Y=23672
X3376 VSS VDD ICV_9 $T=42552 15120 1 0 $X=42552 $Y=13952
X3377 VSS VDD ICV_9 $T=42984 43200 0 0 $X=42984 $Y=43112
X3378 VSS VDD ICV_9 $T=43632 21600 0 0 $X=43632 $Y=21512
X3379 VSS VDD ICV_9 $T=46224 12960 1 0 $X=46224 $Y=11792
X3380 VSS VDD ICV_9 $T=49896 25920 1 0 $X=49896 $Y=24752
X3381 VSS VDD ICV_9 $T=54432 17280 0 0 $X=54432 $Y=17192
X3382 VSS VDD ICV_9 $T=57024 28080 0 0 $X=57024 $Y=27992
X3383 VSS VDD ICV_9 $T=58104 10800 1 0 $X=58104 $Y=9632
X3384 VSS VDD ICV_9 $T=58104 56160 0 0 $X=58104 $Y=56072
X3385 VSS VDD DECAPx4_ASAP7_75t_R $T=4752 34560 1 0 $X=4752 $Y=33392
X3386 VSS VDD DECAPx4_ASAP7_75t_R $T=4752 36720 0 0 $X=4752 $Y=36632
X3387 VSS VDD DECAPx4_ASAP7_75t_R $T=9936 47520 1 0 $X=9936 $Y=46352
X3388 VSS VDD DECAPx4_ASAP7_75t_R $T=12312 36720 1 0 $X=12312 $Y=35552
X3389 VSS VDD DECAPx4_ASAP7_75t_R $T=21816 28080 1 0 $X=21816 $Y=26912
X3390 VSS VDD DECAPx4_ASAP7_75t_R $T=23760 34560 0 0 $X=23760 $Y=34472
X3391 VSS VDD DECAPx4_ASAP7_75t_R $T=24624 6480 1 0 $X=24624 $Y=5312
X3392 VSS VDD DECAPx4_ASAP7_75t_R $T=25056 36720 1 0 $X=25056 $Y=35552
X3393 VSS VDD DECAPx4_ASAP7_75t_R $T=25488 6480 0 0 $X=25488 $Y=6392
X3394 VSS VDD DECAPx4_ASAP7_75t_R $T=37152 47520 0 0 $X=37152 $Y=47432
X3395 VSS VDD DECAPx4_ASAP7_75t_R $T=40608 51840 0 0 $X=40608 $Y=51752
X3396 VSS VDD DECAPx4_ASAP7_75t_R $T=43416 17280 0 0 $X=43416 $Y=17192
X3397 VSS VDD DECAPx4_ASAP7_75t_R $T=50328 25920 0 0 $X=50328 $Y=25832
X3398 VSS VDD DECAPx4_ASAP7_75t_R $T=57888 30240 1 0 $X=57888 $Y=29072
X3399 VSS VDD DECAPx4_ASAP7_75t_R $T=57888 51840 0 0 $X=57888 $Y=51752
X3421 VSS VDD ICV_11 $T=4752 25920 0 0 $X=4752 $Y=25832
X3422 VSS VDD ICV_11 $T=4752 45360 0 0 $X=4752 $Y=45272
X3423 VSS VDD ICV_11 $T=13176 43200 0 0 $X=13176 $Y=43112
X3424 VSS VDD ICV_11 $T=14256 21600 1 0 $X=14256 $Y=20432
X3425 VSS VDD ICV_11 $T=16848 28080 1 0 $X=16848 $Y=26912
X3426 VSS VDD ICV_11 $T=23112 49680 1 0 $X=23112 $Y=48512
X3427 VSS VDD ICV_11 $T=23976 41040 0 0 $X=23976 $Y=40952
X3428 VSS VDD ICV_11 $T=25704 32400 0 0 $X=25704 $Y=32312
X3429 VSS VDD ICV_11 $T=27648 4320 0 0 $X=27648 $Y=4232
X3430 VSS VDD ICV_11 $T=40392 34560 1 0 $X=40392 $Y=33392
X3431 VSS VDD ICV_11 $T=44064 17280 1 0 $X=44064 $Y=16112
X3432 VSS VDD ICV_11 $T=57672 34560 1 0 $X=57672 $Y=33392
X3433 VSS VDD 164 70 INVx1_ASAP7_75t_R $T=8208 21600 0 0 $X=8208 $Y=21512
X3434 VSS VDD 88 91 INVx1_ASAP7_75t_R $T=8208 41040 1 0 $X=8208 $Y=39872
X3435 VSS VDD 82 129 INVx1_ASAP7_75t_R $T=9504 41040 0 0 $X=9504 $Y=40952
X3436 VSS VDD 96 106 INVx1_ASAP7_75t_R $T=10152 25920 0 0 $X=10152 $Y=25832
X3437 VSS VDD 274 497 INVx1_ASAP7_75t_R $T=10152 41040 1 0 $X=10152 $Y=39872
X3438 VSS VDD 501 119 INVx1_ASAP7_75t_R $T=13176 30240 0 0 $X=13176 $Y=30152
X3439 VSS VDD 120 626 INVx1_ASAP7_75t_R $T=13824 15120 0 0 $X=13824 $Y=15032
X3440 VSS VDD 133 602 INVx1_ASAP7_75t_R $T=15984 12960 1 0 $X=15984 $Y=11792
X3441 VSS VDD 23 627 INVx1_ASAP7_75t_R $T=18144 34560 0 0 $X=18144 $Y=34472
X3442 VSS VDD 510 165 INVx1_ASAP7_75t_R $T=18360 23760 1 0 $X=18360 $Y=22592
X3443 VSS VDD 29 628 INVx1_ASAP7_75t_R $T=18576 36720 0 0 $X=18576 $Y=36632
X3444 VSS VDD 163 168 INVx1_ASAP7_75t_R $T=18792 19440 1 0 $X=18792 $Y=18272
X3445 VSS VDD 5 171 INVx1_ASAP7_75t_R $T=19008 41040 0 0 $X=19008 $Y=40952
X3446 VSS VDD 9 206 INVx1_ASAP7_75t_R $T=22248 28080 0 0 $X=22248 $Y=27992
X3447 VSS VDD 199 629 INVx1_ASAP7_75t_R $T=22464 21600 0 0 $X=22464 $Y=21512
X3448 VSS VDD 607 202 INVx1_ASAP7_75t_R $T=22896 15120 0 0 $X=22896 $Y=15032
X3449 VSS VDD 12 608 INVx1_ASAP7_75t_R $T=23328 23760 1 0 $X=23328 $Y=22592
X3450 VSS VDD 194 513 INVx1_ASAP7_75t_R $T=24408 17280 1 0 $X=24408 $Y=16112
X3451 VSS VDD 209 207 INVx1_ASAP7_75t_R $T=25056 17280 1 0 $X=25056 $Y=16112
X3452 VSS VDD 234 538 INVx1_ASAP7_75t_R $T=27000 32400 1 0 $X=27000 $Y=31232
X3453 VSS VDD 233 227 INVx1_ASAP7_75t_R $T=27216 12960 0 0 $X=27216 $Y=12872
X3454 VSS VDD 237 542 INVx1_ASAP7_75t_R $T=27648 10800 0 0 $X=27648 $Y=10712
X3455 VSS VDD 262 268 INVx1_ASAP7_75t_R $T=30240 34560 0 0 $X=30240 $Y=34472
X3456 VSS VDD 288 239 INVx1_ASAP7_75t_R $T=30240 41040 1 0 $X=30240 $Y=39872
X3457 VSS VDD 293 273 INVx1_ASAP7_75t_R $T=34560 25920 1 0 $X=34560 $Y=24752
X3458 VSS VDD 358 285 INVx1_ASAP7_75t_R $T=34992 28080 0 0 $X=34992 $Y=27992
X3459 VSS VDD 307 338 INVx1_ASAP7_75t_R $T=35856 30240 0 0 $X=35856 $Y=30152
X3460 VSS VDD 630 321 INVx1_ASAP7_75t_R $T=36504 30240 0 0 $X=36504 $Y=30152
X3461 VSS VDD 332 336 INVx1_ASAP7_75t_R $T=37368 32400 1 0 $X=37368 $Y=31232
X3462 VSS VDD 330 305 INVx1_ASAP7_75t_R $T=37368 47520 1 0 $X=37368 $Y=46352
X3463 VSS VDD 365 567 INVx1_ASAP7_75t_R $T=41688 8640 0 0 $X=41688 $Y=8552
X3464 VSS VDD 378 352 INVx1_ASAP7_75t_R $T=43200 19440 0 0 $X=43200 $Y=19352
X3465 VSS VDD 396 366 INVx1_ASAP7_75t_R $T=43416 17280 1 0 $X=43416 $Y=16112
X3466 VSS VDD 393 385 INVx1_ASAP7_75t_R $T=44496 38880 1 0 $X=44496 $Y=37712
X3467 VSS VDD 383 616 INVx1_ASAP7_75t_R $T=45792 41040 1 0 $X=45792 $Y=39872
X3468 VSS VDD 395 580 INVx1_ASAP7_75t_R $T=46224 8640 1 0 $X=46224 $Y=7472
X3469 VSS VDD 410 392 INVx1_ASAP7_75t_R $T=46656 12960 0 0 $X=46656 $Y=12872
X3470 VSS VDD 416 439 INVx1_ASAP7_75t_R $T=47952 41040 1 0 $X=47952 $Y=39872
X3471 VSS VDD 427 631 INVx1_ASAP7_75t_R $T=48384 30240 0 0 $X=48384 $Y=30152
X3472 VSS VDD 429 445 INVx1_ASAP7_75t_R $T=48600 41040 0 0 $X=48600 $Y=40952
X3473 VSS VDD 434 413 INVx1_ASAP7_75t_R $T=49680 25920 0 0 $X=49680 $Y=25832
X3474 VSS VDD 444 426 INVx1_ASAP7_75t_R $T=49896 23760 1 0 $X=49896 $Y=22592
X3475 VSS VDD 442 400 INVx1_ASAP7_75t_R $T=49896 23760 0 0 $X=49896 $Y=23672
X3476 VSS VDD 459 438 INVx1_ASAP7_75t_R $T=53568 15120 1 0 $X=53568 $Y=13952
X3477 VSS VDD 466 475 INVx1_ASAP7_75t_R $T=55512 30240 0 0 $X=55512 $Y=30152
X3478 VSS VDD 457 473 INVx1_ASAP7_75t_R $T=56376 17280 0 0 $X=56376 $Y=17192
X3479 VSS VDD 471 474 INVx1_ASAP7_75t_R $T=56376 28080 0 0 $X=56376 $Y=27992
X3480 VSS VDD 448 477 INVx1_ASAP7_75t_R $T=56592 21600 1 0 $X=56592 $Y=20432
X3481 VSS VDD 462 479 INVx1_ASAP7_75t_R $T=57240 12960 1 0 $X=57240 $Y=11792
X3482 VSS VDD 452 484 INVx1_ASAP7_75t_R $T=57672 23760 1 0 $X=57672 $Y=22592
X3502 VSS VDD ICV_13 $T=10584 30240 0 0 $X=10584 $Y=30152
X3503 VSS VDD ICV_13 $T=14256 8640 1 0 $X=14256 $Y=7472
X3504 VSS VDD ICV_13 $T=15336 34560 1 0 $X=15336 $Y=33392
X3505 VSS VDD ICV_13 $T=16848 49680 1 0 $X=16848 $Y=48512
X3506 VSS VDD ICV_13 $T=17064 6480 0 0 $X=17064 $Y=6392
X3507 VSS VDD ICV_13 $T=17928 45360 0 0 $X=17928 $Y=45272
X3508 VSS VDD ICV_13 $T=23976 4320 0 0 $X=23976 $Y=4232
X3509 VSS VDD ICV_13 $T=24408 38880 0 0 $X=24408 $Y=38792
X3510 VSS VDD ICV_13 $T=26568 56160 1 0 $X=26568 $Y=54992
X3511 VSS VDD ICV_13 $T=27864 12960 0 0 $X=27864 $Y=12872
X3512 VSS VDD ICV_13 $T=28296 30240 1 0 $X=28296 $Y=29072
X3513 VSS VDD ICV_13 $T=29376 21600 0 0 $X=29376 $Y=21512
X3514 VSS VDD ICV_13 $T=30888 54000 0 0 $X=30888 $Y=53912
X3515 VSS VDD ICV_13 $T=31536 19440 1 0 $X=31536 $Y=18272
X3516 VSS VDD ICV_13 $T=38232 36720 0 0 $X=38232 $Y=36632
X3517 VSS VDD ICV_13 $T=40392 54000 0 0 $X=40392 $Y=53912
X3518 VSS VDD ICV_13 $T=49464 28080 0 0 $X=49464 $Y=27992
X3519 VSS VDD ICV_13 $T=57456 41040 0 0 $X=57456 $Y=40952
X3520 VSS VDD 147 145 ICV_14 $T=17280 17280 1 0 $X=17280 $Y=16112
X3521 VSS VDD 155 157 ICV_14 $T=17928 25920 0 0 $X=17928 $Y=25832
X3522 VSS VDD 26 517 ICV_14 $T=21600 38880 1 0 $X=21600 $Y=37712
X3523 VSS VDD 21 44 ICV_14 $T=40392 23760 1 0 $X=40392 $Y=22592
X3524 VSS VDD 415 582 ICV_14 $T=46440 41040 1 0 $X=46440 $Y=39872
X3525 VSS VDD 441 411 ICV_14 $T=47952 17280 1 0 $X=47952 $Y=16112
X3526 VSS VDD 454 428 ICV_14 $T=49896 17280 0 0 $X=49896 $Y=17192
X3527 VSS VDD 423 469 ICV_14 $T=55296 12960 1 0 $X=55296 $Y=11792
X3528 VSS VDD 463 470 ICV_14 $T=55296 23760 0 0 $X=55296 $Y=23672
X3529 VSS VDD 478 481 ICV_14 $T=57240 30240 0 0 $X=57240 $Y=30152
X3530 VSS VDD ICV_15 $T=4752 23760 1 0 $X=4752 $Y=22592
X3531 VSS VDD ICV_15 $T=4752 30240 0 0 $X=4752 $Y=30152
X3532 VSS VDD ICV_15 $T=4752 47520 1 0 $X=4752 $Y=46352
X3533 VSS VDD ICV_15 $T=11880 21600 0 0 $X=11880 $Y=21512
X3534 VSS VDD ICV_15 $T=15552 23760 1 0 $X=15552 $Y=22592
X3535 VSS VDD ICV_15 $T=26136 60480 1 0 $X=26136 $Y=59312
X3536 VSS VDD ICV_15 $T=26568 58320 1 0 $X=26568 $Y=57152
X3537 VSS VDD ICV_15 $T=28080 6480 1 0 $X=28080 $Y=5312
X3538 VSS VDD ICV_15 $T=30456 45360 1 0 $X=30456 $Y=44192
X3539 VSS VDD ICV_15 $T=34560 32400 1 0 $X=34560 $Y=31232
X3540 VSS VDD ICV_15 $T=35424 23760 1 0 $X=35424 $Y=22592
X3541 VSS VDD ICV_15 $T=36072 51840 0 0 $X=36072 $Y=51752
X3542 VSS VDD ICV_15 $T=39528 12960 0 0 $X=39528 $Y=12872
X3543 VSS VDD ICV_15 $T=45792 41040 0 0 $X=45792 $Y=40952
X3544 VSS VDD ICV_15 $T=57240 12960 0 0 $X=57240 $Y=12872
X3545 VSS VDD ICV_15 $T=57240 21600 1 0 $X=57240 $Y=20432
X3672 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 25920 1 0 $X=4752 $Y=24752
X3673 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 28080 0 0 $X=4752 $Y=27992
X3674 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 30240 1 0 $X=4752 $Y=29072
X3675 VSS VDD DECAPx6_ASAP7_75t_R $T=4752 38880 0 0 $X=4752 $Y=38792
X3676 VSS VDD DECAPx6_ASAP7_75t_R $T=10800 41040 1 0 $X=10800 $Y=39872
X3677 VSS VDD DECAPx6_ASAP7_75t_R $T=11448 38880 0 0 $X=11448 $Y=38792
X3678 VSS VDD DECAPx6_ASAP7_75t_R $T=11664 38880 1 0 $X=11664 $Y=37712
X3679 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 4320 0 0 $X=14256 $Y=4232
X3680 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 17280 1 0 $X=14256 $Y=16112
X3681 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 49680 0 0 $X=14256 $Y=49592
X3682 VSS VDD DECAPx6_ASAP7_75t_R $T=14256 51840 1 0 $X=14256 $Y=50672
X3683 VSS VDD DECAPx6_ASAP7_75t_R $T=14688 19440 1 0 $X=14688 $Y=18272
X3684 VSS VDD DECAPx6_ASAP7_75t_R $T=15984 30240 0 0 $X=15984 $Y=30152
X3685 VSS VDD DECAPx6_ASAP7_75t_R $T=16200 23760 0 0 $X=16200 $Y=23672
X3686 VSS VDD DECAPx6_ASAP7_75t_R $T=19872 4320 0 0 $X=19872 $Y=4232
X3687 VSS VDD DECAPx6_ASAP7_75t_R $T=22032 30240 0 0 $X=22032 $Y=30152
X3688 VSS VDD DECAPx6_ASAP7_75t_R $T=22896 49680 0 0 $X=22896 $Y=49592
X3689 VSS VDD DECAPx6_ASAP7_75t_R $T=23328 45360 1 0 $X=23328 $Y=44192
X3690 VSS VDD DECAPx6_ASAP7_75t_R $T=23328 45360 0 0 $X=23328 $Y=45272
X3691 VSS VDD DECAPx6_ASAP7_75t_R $T=24624 38880 1 0 $X=24624 $Y=37712
X3692 VSS VDD DECAPx6_ASAP7_75t_R $T=26352 17280 0 0 $X=26352 $Y=17192
X3693 VSS VDD DECAPx6_ASAP7_75t_R $T=26784 15120 1 0 $X=26784 $Y=13952
X3694 VSS VDD DECAPx6_ASAP7_75t_R $T=27000 56160 0 0 $X=27000 $Y=56072
X3695 VSS VDD DECAPx6_ASAP7_75t_R $T=27648 32400 1 0 $X=27648 $Y=31232
X3696 VSS VDD DECAPx6_ASAP7_75t_R $T=30240 12960 1 0 $X=30240 $Y=11792
X3697 VSS VDD DECAPx6_ASAP7_75t_R $T=32616 58320 1 0 $X=32616 $Y=57152
X3698 VSS VDD DECAPx6_ASAP7_75t_R $T=32832 38880 1 0 $X=32832 $Y=37712
X3699 VSS VDD DECAPx6_ASAP7_75t_R $T=35208 54000 1 0 $X=35208 $Y=52832
X3700 VSS VDD DECAPx6_ASAP7_75t_R $T=36072 38880 0 0 $X=36072 $Y=38792
X3701 VSS VDD DECAPx6_ASAP7_75t_R $T=36288 21600 0 0 $X=36288 $Y=21512
X3702 VSS VDD DECAPx6_ASAP7_75t_R $T=36720 21600 1 0 $X=36720 $Y=20432
X3703 VSS VDD DECAPx6_ASAP7_75t_R $T=40392 45360 1 0 $X=40392 $Y=44192
X3704 VSS VDD DECAPx6_ASAP7_75t_R $T=40392 45360 0 0 $X=40392 $Y=45272
X3705 VSS VDD DECAPx6_ASAP7_75t_R $T=40824 28080 1 0 $X=40824 $Y=26912
X3706 VSS VDD DECAPx6_ASAP7_75t_R $T=43632 49680 0 0 $X=43632 $Y=49592
X3707 VSS VDD DECAPx6_ASAP7_75t_R $T=44496 19440 1 0 $X=44496 $Y=18272
X3708 VSS VDD DECAPx6_ASAP7_75t_R $T=46008 15120 1 0 $X=46008 $Y=13952
X3709 VSS VDD DECAPx6_ASAP7_75t_R $T=46656 51840 1 0 $X=46656 $Y=50672
X3710 VSS VDD DECAPx6_ASAP7_75t_R $T=47304 45360 1 0 $X=47304 $Y=44192
X3711 VSS VDD DECAPx6_ASAP7_75t_R $T=55512 45360 0 0 $X=55512 $Y=45272
X3712 VSS VDD DECAPx6_ASAP7_75t_R $T=55728 6480 0 0 $X=55728 $Y=6392
X3713 VSS VDD DECAPx6_ASAP7_75t_R $T=55728 49680 1 0 $X=55728 $Y=48512
X3714 VSS VDD DECAPx6_ASAP7_75t_R $T=55944 10800 0 0 $X=55944 $Y=10712
X3715 VSS VDD DECAPx6_ASAP7_75t_R $T=55944 32400 1 0 $X=55944 $Y=31232
X3716 VSS VDD DECAPx6_ASAP7_75t_R $T=56376 8640 1 0 $X=56376 $Y=7472
X3717 VSS VDD DECAPx6_ASAP7_75t_R $T=57024 6480 1 0 $X=57024 $Y=5312
X3810 VSS VDD ICV_18 $T=4752 28080 1 0 $X=4752 $Y=26912
X3811 VSS VDD ICV_18 $T=4752 41040 1 0 $X=4752 $Y=39872
X3812 VSS VDD ICV_18 $T=11448 32400 0 0 $X=11448 $Y=32312
X3813 VSS VDD ICV_18 $T=12528 41040 0 0 $X=12528 $Y=40952
X3814 VSS VDD ICV_18 $T=14256 8640 0 0 $X=14256 $Y=8552
X3815 VSS VDD ICV_18 $T=21384 25920 0 0 $X=21384 $Y=25832
X3816 VSS VDD ICV_18 $T=29592 49680 0 0 $X=29592 $Y=49592
X3817 VSS VDD ICV_18 $T=30240 56160 1 0 $X=30240 $Y=54992
X3818 VSS VDD ICV_18 $T=32184 34560 0 0 $X=32184 $Y=34472
X3819 VSS VDD ICV_18 $T=36288 15120 0 0 $X=36288 $Y=15032
X3820 VSS VDD ICV_18 $T=38016 47520 1 0 $X=38016 $Y=46352
X3821 VSS VDD ICV_18 $T=42768 8640 1 0 $X=42768 $Y=7472
X3822 VSS VDD ICV_18 $T=56592 28080 1 0 $X=56592 $Y=26912
X3823 VSS VDD ICV_18 $T=56592 51840 1 0 $X=56592 $Y=50672
X3824 VSS VDD ICV_19 $T=12960 17280 0 0 $X=12960 $Y=17192
X3825 VSS VDD ICV_19 $T=25704 17280 1 0 $X=25704 $Y=16112
X3826 VSS VDD ICV_19 $T=28296 10800 0 0 $X=28296 $Y=10712
X3827 VSS VDD ICV_19 $T=41040 25920 0 0 $X=41040 $Y=25832
X3828 VSS VDD ICV_19 $T=52920 30240 1 0 $X=52920 $Y=29072
X3829 VSS VDD ICV_19 $T=53352 21600 0 0 $X=53352 $Y=21512
X3830 VSS VDD ICV_19 $T=56160 17280 1 0 $X=56160 $Y=16112
X3831 VSS VDD ICV_19 $T=56160 25920 1 0 $X=56160 $Y=24752
X3832 VSS VDD ICV_19 $T=56160 38880 1 0 $X=56160 $Y=37712
X3833 VSS VDD ICV_19 $T=56160 43200 0 0 $X=56160 $Y=43112
X3849 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 17280 0 0 $X=4752 $Y=17192
X3850 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 19440 1 0 $X=4752 $Y=18272
X3851 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 19440 0 0 $X=4752 $Y=19352
X3852 VSS VDD DECAPx10_ASAP7_75t_R $T=4752 38880 1 0 $X=4752 $Y=37712
X3853 VSS VDD DECAPx10_ASAP7_75t_R $T=10584 34560 1 0 $X=10584 $Y=33392
X3854 VSS VDD DECAPx10_ASAP7_75t_R $T=11232 45360 1 0 $X=11232 $Y=44192
X3855 VSS VDD DECAPx10_ASAP7_75t_R $T=11664 34560 0 0 $X=11664 $Y=34472
X3856 VSS VDD DECAPx10_ASAP7_75t_R $T=12312 19440 0 0 $X=12312 $Y=19352
X3857 VSS VDD DECAPx10_ASAP7_75t_R $T=14256 6480 1 0 $X=14256 $Y=5312
X3858 VSS VDD DECAPx10_ASAP7_75t_R $T=14256 10800 1 0 $X=14256 $Y=9632
X3859 VSS VDD DECAPx10_ASAP7_75t_R $T=17496 15120 0 0 $X=17496 $Y=15032
X3860 VSS VDD DECAPx10_ASAP7_75t_R $T=17928 12960 1 0 $X=17928 $Y=11792
X3861 VSS VDD DECAPx10_ASAP7_75t_R $T=20088 32400 1 0 $X=20088 $Y=31232
X3862 VSS VDD DECAPx10_ASAP7_75t_R $T=20088 43200 0 0 $X=20088 $Y=43112
X3863 VSS VDD DECAPx10_ASAP7_75t_R $T=21816 47520 0 0 $X=21816 $Y=47432
X3864 VSS VDD DECAPx10_ASAP7_75t_R $T=23976 23760 1 0 $X=23976 $Y=22592
X3865 VSS VDD DECAPx10_ASAP7_75t_R $T=24624 21600 0 0 $X=24624 $Y=21512
X3866 VSS VDD DECAPx10_ASAP7_75t_R $T=26136 25920 0 0 $X=26136 $Y=25832
X3867 VSS VDD DECAPx10_ASAP7_75t_R $T=26784 19440 1 0 $X=26784 $Y=18272
X3868 VSS VDD DECAPx10_ASAP7_75t_R $T=27000 30240 0 0 $X=27000 $Y=30152
X3869 VSS VDD DECAPx10_ASAP7_75t_R $T=28944 51840 0 0 $X=28944 $Y=51752
X3870 VSS VDD DECAPx10_ASAP7_75t_R $T=29808 28080 1 0 $X=29808 $Y=26912
X3871 VSS VDD DECAPx10_ASAP7_75t_R $T=30456 47520 0 0 $X=30456 $Y=47432
X3872 VSS VDD DECAPx10_ASAP7_75t_R $T=31104 47520 1 0 $X=31104 $Y=46352
X3873 VSS VDD DECAPx10_ASAP7_75t_R $T=31320 36720 0 0 $X=31320 $Y=36632
X3874 VSS VDD DECAPx10_ASAP7_75t_R $T=32832 45360 0 0 $X=32832 $Y=45272
X3875 VSS VDD DECAPx10_ASAP7_75t_R $T=34128 58320 0 0 $X=34128 $Y=58232
X3876 VSS VDD DECAPx10_ASAP7_75t_R $T=34344 15120 1 0 $X=34344 $Y=13952
X3877 VSS VDD DECAPx10_ASAP7_75t_R $T=34776 12960 0 0 $X=34776 $Y=12872
X3878 VSS VDD DECAPx10_ASAP7_75t_R $T=34992 12960 1 0 $X=34992 $Y=11792
X3879 VSS VDD DECAPx10_ASAP7_75t_R $T=35208 17280 0 0 $X=35208 $Y=17192
X3880 VSS VDD DECAPx10_ASAP7_75t_R $T=35424 8640 0 0 $X=35424 $Y=8552
X3881 VSS VDD DECAPx10_ASAP7_75t_R $T=35640 54000 0 0 $X=35640 $Y=53912
X3882 VSS VDD DECAPx10_ASAP7_75t_R $T=36072 8640 1 0 $X=36072 $Y=7472
X3883 VSS VDD DECAPx10_ASAP7_75t_R $T=36288 49680 1 0 $X=36288 $Y=48512
X3884 VSS VDD DECAPx10_ASAP7_75t_R $T=37584 41040 0 0 $X=37584 $Y=40952
X3885 VSS VDD DECAPx10_ASAP7_75t_R $T=38448 34560 0 0 $X=38448 $Y=34472
X3886 VSS VDD DECAPx10_ASAP7_75t_R $T=39312 43200 1 0 $X=39312 $Y=42032
X3887 VSS VDD DECAPx10_ASAP7_75t_R $T=41040 21600 1 0 $X=41040 $Y=20432
X3888 VSS VDD DECAPx10_ASAP7_75t_R $T=42768 25920 1 0 $X=42768 $Y=24752
X3889 VSS VDD DECAPx10_ASAP7_75t_R $T=43632 32400 0 0 $X=43632 $Y=32312
X3890 VSS VDD DECAPx10_ASAP7_75t_R $T=43848 19440 0 0 $X=43848 $Y=19352
X3891 VSS VDD DECAPx10_ASAP7_75t_R $T=52704 41040 0 0 $X=52704 $Y=40952
X3892 VSS VDD DECAPx10_ASAP7_75t_R $T=53568 25920 0 0 $X=53568 $Y=25832
X3893 VSS VDD DECAPx10_ASAP7_75t_R $T=54000 47520 0 0 $X=54000 $Y=47432
X3894 VSS VDD DECAPx10_ASAP7_75t_R $T=54216 15120 1 0 $X=54216 $Y=13952
X3895 VSS VDD DECAPx10_ASAP7_75t_R $T=54864 38880 0 0 $X=54864 $Y=38792
X4107 VSS VDD 496 143 10 NAND2x1_ASAP7_75t_R $T=14256 23760 1 0 $X=14256 $Y=22592
X4108 VSS VDD 7 6 153 NAND2x1_ASAP7_75t_R $T=15984 41040 0 0 $X=15984 $Y=40952
X4109 VSS VDD 496 99 137 NAND2x1_ASAP7_75t_R $T=17496 28080 0 0 $X=17496 $Y=27992
X4110 VSS VDD 7 509 148 NAND2x1_ASAP7_75t_R $T=17928 34560 1 0 $X=17928 $Y=33392
X4111 VSS VDD 22 17 187 NAND2x1_ASAP7_75t_R $T=19224 28080 1 0 $X=19224 $Y=26912
X4112 VSS VDD 177 188 666 NAND2x1_ASAP7_75t_R $T=19656 10800 1 0 $X=19656 $Y=9632
X4113 VSS VDD 513 190 16 NAND2x1_ASAP7_75t_R $T=20088 19440 0 0 $X=20088 $Y=19352
X4114 VSS VDD 178 157 173 NAND2x1_ASAP7_75t_R $T=20088 25920 0 0 $X=20088 $Y=25832
X4115 VSS VDD 12 248 518 NAND2x1_ASAP7_75t_R $T=21600 19440 0 0 $X=21600 $Y=19352
X4116 VSS VDD 633 188 520 NAND2x1_ASAP7_75t_R $T=22248 10800 1 0 $X=22248 $Y=9632
X4117 VSS VDD 24 139 215 NAND2x1_ASAP7_75t_R $T=23760 30240 1 0 $X=23760 $Y=29072
X4118 VSS VDD 178 526 528 NAND2x1_ASAP7_75t_R $T=24840 25920 0 0 $X=24840 $Y=25832
X4119 VSS VDD 218 188 223 NAND2x1_ASAP7_75t_R $T=25272 8640 0 0 $X=25272 $Y=8552
X4120 VSS VDD 239 257 244 NAND2x1_ASAP7_75t_R $T=28944 41040 1 0 $X=28944 $Y=39872
X4121 VSS VDD 251 188 241 NAND2x1_ASAP7_75t_R $T=29160 6480 0 0 $X=29160 $Y=6392
X4122 VSS VDD 256 188 266 NAND2x1_ASAP7_75t_R $T=29592 17280 1 0 $X=29592 $Y=16112
X4123 VSS VDD 273 324 271 NAND2x1_ASAP7_75t_R $T=31536 25920 1 0 $X=31536 $Y=24752
X4124 VSS VDD 285 298 295 NAND2x1_ASAP7_75t_R $T=32832 30240 0 0 $X=32832 $Y=30152
X4125 VSS VDD 275 188 613 NAND2x1_ASAP7_75t_R $T=33048 15120 1 0 $X=33048 $Y=13952
X4126 VSS VDD 290 188 561 NAND2x1_ASAP7_75t_R $T=33696 10800 1 0 $X=33696 $Y=9632
X4127 VSS VDD 320 188 311 NAND2x1_ASAP7_75t_R $T=34128 6480 1 0 $X=34128 $Y=5312
X4128 VSS VDD 303 324 286 NAND2x1_ASAP7_75t_R $T=34128 23760 1 0 $X=34128 $Y=22592
X4129 VSS VDD 258 116 312 NAND2x1_ASAP7_75t_R $T=34128 41040 0 0 $X=34128 $Y=40952
X4130 VSS VDD 308 630 565 NAND2x1_ASAP7_75t_R $T=36288 28080 1 0 $X=36288 $Y=26912
X4131 VSS VDD 328 258 340 NAND2x1_ASAP7_75t_R $T=37152 34560 0 0 $X=37152 $Y=34472
X4132 VSS VDD 330 339 319 NAND2x1_ASAP7_75t_R $T=37152 58320 1 0 $X=37152 $Y=57152
X4133 VSS VDD 336 344 346 NAND2x1_ASAP7_75t_R $T=38016 32400 1 0 $X=38016 $Y=31232
X4134 VSS VDD 350 358 332 NAND2x1_ASAP7_75t_R $T=38448 25920 0 0 $X=38448 $Y=25832
X4135 VSS VDD 365 364 623 NAND2x1_ASAP7_75t_R $T=43632 10800 0 0 $X=43632 $Y=10712
X4136 VSS VDD 394 402 388 NAND2x1_ASAP7_75t_R $T=44712 56160 1 0 $X=44712 $Y=54992
X4137 VSS VDD 395 364 667 NAND2x1_ASAP7_75t_R $T=45144 10800 0 0 $X=45144 $Y=10712
X4138 VSS VDD 423 364 656 NAND2x1_ASAP7_75t_R $T=48168 12960 1 0 $X=48168 $Y=11792
X4139 VSS VDD 424 578 586 NAND2x1_ASAP7_75t_R $T=48168 43200 1 0 $X=48168 $Y=42032
X4140 VSS VDD 402 440 364 NAND2x1_ASAP7_75t_R $T=49896 32400 0 0 $X=49896 $Y=32312
X4141 VSS VDD 448 364 594 NAND2x1_ASAP7_75t_R $T=50976 21600 1 0 $X=50976 $Y=20432
X4142 VSS VDD 452 364 668 NAND2x1_ASAP7_75t_R $T=52056 21600 0 0 $X=52056 $Y=21512
X4143 VSS VDD 457 364 464 NAND2x1_ASAP7_75t_R $T=53136 17280 0 0 $X=53136 $Y=17192
X4144 VSS VDD 462 364 467 NAND2x1_ASAP7_75t_R $T=54000 12960 1 0 $X=54000 $Y=11792
X4145 VSS VDD 463 364 468 NAND2x1_ASAP7_75t_R $T=54000 23760 0 0 $X=54000 $Y=23672
X4197 VSS VDD ICV_23 $T=20520 41040 1 0 $X=20520 $Y=39872
X4198 VSS VDD ICV_23 $T=23112 23760 0 0 $X=23112 $Y=23672
X4199 VSS VDD ICV_23 $T=28512 43200 1 0 $X=28512 $Y=42032
X4200 VSS VDD ICV_23 $T=52488 19440 1 0 $X=52488 $Y=18272
X4201 VSS VDD ICV_23 $T=52920 19440 0 0 $X=52920 $Y=19352
X4202 VSS VDD ICV_23 $T=52920 43200 1 0 $X=52920 $Y=42032
X4203 VSS VDD ICV_23 $T=52920 45360 1 0 $X=52920 $Y=44192
X4204 VSS VDD ICV_23 $T=52920 58320 0 0 $X=52920 $Y=58232
X4205 VSS VDD ICV_23 $T=53136 47520 1 0 $X=53136 $Y=46352
X4206 VSS VDD ICV_23 $T=53136 54000 1 0 $X=53136 $Y=52832
X4323 VSS VDD 92 71 94 DFFHQNx1_ASAP7_75t_R $T=4752 32400 1 0 $X=4752 $Y=31232
X4324 VSS VDD 8 72 90 DFFHQNx1_ASAP7_75t_R $T=4752 34560 0 0 $X=4752 $Y=34472
X4325 VSS VDD 8 490 495 DFFHQNx1_ASAP7_75t_R $T=6480 36720 1 0 $X=6480 $Y=35552
X4326 VSS VDD 92 97 123 DFFHQNx1_ASAP7_75t_R $T=8424 28080 0 0 $X=8424 $Y=27992
X4327 VSS VDD 210 114 125 DFFHQNx1_ASAP7_75t_R $T=10368 19440 1 0 $X=10368 $Y=18272
X4328 VSS VDD 92 500 493 DFFHQNx1_ASAP7_75t_R $T=11880 23760 0 0 $X=11880 $Y=23672
X4329 VSS VDD 210 504 154 DFFHQNx1_ASAP7_75t_R $T=14040 12960 0 0 $X=14040 $Y=12872
X4330 VSS VDD 8 128 7 DFFHQNx1_ASAP7_75t_R $T=14256 36720 0 0 $X=14256 $Y=36632
X4331 VSS VDD 8 131 6 DFFHQNx1_ASAP7_75t_R $T=14472 47520 1 0 $X=14472 $Y=46352
X4332 VSS VDD 92 165 164 DFFHQNx1_ASAP7_75t_R $T=14688 21600 0 0 $X=14688 $Y=21512
X4333 VSS VDD 210 150 177 DFFHQNx1_ASAP7_75t_R $T=16848 8640 1 0 $X=16848 $Y=7472
X4334 VSS VDD 92 55 12 DFFHQNx1_ASAP7_75t_R $T=19008 23760 1 0 $X=19008 $Y=22592
X4335 VSS VDD 210 180 607 DFFHQNx1_ASAP7_75t_R $T=19440 12960 0 0 $X=19440 $Y=12872
X4336 VSS VDD 8 635 29 DFFHQNx1_ASAP7_75t_R $T=19440 36720 1 0 $X=19440 $Y=35552
X4337 VSS VDD 210 183 633 DFFHQNx1_ASAP7_75t_R $T=19656 6480 0 0 $X=19656 $Y=6392
X4338 VSS VDD 8 182 5 DFFHQNx1_ASAP7_75t_R $T=19656 41040 0 0 $X=19656 $Y=40952
X4339 VSS VDD 32 516 4 DFFHQNx1_ASAP7_75t_R $T=20736 47520 1 0 $X=20736 $Y=46352
X4340 VSS VDD 92 609 178 DFFHQNx1_ASAP7_75t_R $T=25488 28080 1 0 $X=25488 $Y=26912
X4341 VSS VDD 210 227 237 DFFHQNx1_ASAP7_75t_R $T=25920 12960 1 0 $X=25920 $Y=11792
X4342 VSS VDD 32 230 262 DFFHQNx1_ASAP7_75t_R $T=25920 34560 0 0 $X=25920 $Y=34472
X4343 VSS VDD 92 541 234 DFFHQNx1_ASAP7_75t_R $T=26352 28080 0 0 $X=26352 $Y=27992
X4344 VSS VDD 32 539 247 DFFHQNx1_ASAP7_75t_R $T=27000 36720 0 0 $X=27000 $Y=36632
X4345 VSS VDD 210 240 251 DFFHQNx1_ASAP7_75t_R $T=27216 8640 1 0 $X=27216 $Y=7472
X4346 VSS VDD 32 249 288 DFFHQNx1_ASAP7_75t_R $T=29160 41040 0 0 $X=29160 $Y=40952
X4347 VSS VDD 92 267 293 DFFHQNx1_ASAP7_75t_R $T=29808 23760 1 0 $X=29808 $Y=22592
X4348 VSS VDD 372 302 275 DFFHQNx1_ASAP7_75t_R $T=30456 12960 0 0 $X=30456 $Y=12872
X4349 VSS VDD 92 553 358 DFFHQNx1_ASAP7_75t_R $T=30888 30240 1 0 $X=30888 $Y=29072
X4350 VSS VDD 381 284 303 DFFHQNx1_ASAP7_75t_R $T=31968 21600 0 0 $X=31968 $Y=21512
X4351 VSS VDD 372 560 290 DFFHQNx1_ASAP7_75t_R $T=33480 10800 0 0 $X=33480 $Y=10712
X4352 VSS VDD 372 640 326 DFFHQNx1_ASAP7_75t_R $T=34128 19440 1 0 $X=34128 $Y=18272
X4353 VSS VDD 46 564 357 DFFHQNx1_ASAP7_75t_R $T=36072 49680 0 0 $X=36072 $Y=49592
X4354 VSS VDD 46 345 569 DFFHQNx1_ASAP7_75t_R $T=38016 56160 0 0 $X=38016 $Y=56072
X4355 VSS VDD 48 342 371 DFFHQNx1_ASAP7_75t_R $T=38664 36720 1 0 $X=38664 $Y=35552
X4356 VSS VDD 46 351 376 DFFHQNx1_ASAP7_75t_R $T=38880 54000 1 0 $X=38880 $Y=52832
X4357 VSS VDD 372 354 365 DFFHQNx1_ASAP7_75t_R $T=39312 10800 0 0 $X=39312 $Y=10712
X4358 VSS VDD 381 644 350 DFFHQNx1_ASAP7_75t_R $T=39312 21600 0 0 $X=39312 $Y=21512
X4359 VSS VDD 372 361 396 DFFHQNx1_ASAP7_75t_R $T=39744 15120 0 0 $X=39744 $Y=15032
X4360 VSS VDD 46 646 573 DFFHQNx1_ASAP7_75t_R $T=39744 58320 1 0 $X=39744 $Y=57152
X4361 VSS VDD 372 566 378 DFFHQNx1_ASAP7_75t_R $T=40176 19440 1 0 $X=40176 $Y=18272
X4362 VSS VDD 372 377 410 DFFHQNx1_ASAP7_75t_R $T=42336 12960 0 0 $X=42336 $Y=12872
X4363 VSS VDD 46 572 386 DFFHQNx1_ASAP7_75t_R $T=42336 51840 1 0 $X=42336 $Y=50672
X4364 VSS VDD 48 384 420 DFFHQNx1_ASAP7_75t_R $T=43848 32400 1 0 $X=43848 $Y=31232
X4365 VSS VDD 372 576 395 DFFHQNx1_ASAP7_75t_R $T=44280 10800 1 0 $X=44280 $Y=9632
X4366 VSS VDD 381 403 434 DFFHQNx1_ASAP7_75t_R $T=44928 25920 0 0 $X=44928 $Y=25832
X4367 VSS VDD 48 581 393 DFFHQNx1_ASAP7_75t_R $T=44928 36720 0 0 $X=44928 $Y=36632
X4368 VSS VDD 381 407 444 DFFHQNx1_ASAP7_75t_R $T=45144 23760 0 0 $X=45144 $Y=23672
X4369 VSS VDD 372 412 441 DFFHQNx1_ASAP7_75t_R $T=45576 17280 0 0 $X=45576 $Y=17192
X4370 VSS VDD 381 418 442 DFFHQNx1_ASAP7_75t_R $T=45576 23760 1 0 $X=45576 $Y=22592
X4371 VSS VDD 48 648 414 DFFHQNx1_ASAP7_75t_R $T=47088 34560 0 0 $X=47088 $Y=34472
X4372 VSS VDD 46 655 436 DFFHQNx1_ASAP7_75t_R $T=47088 58320 1 0 $X=47088 $Y=57152
X4373 VSS VDD 381 589 454 DFFHQNx1_ASAP7_75t_R $T=48168 19440 1 0 $X=48168 $Y=18272
X4374 VSS VDD 46 431 421 DFFHQNx1_ASAP7_75t_R $T=48816 54000 1 0 $X=48816 $Y=52832
X4375 VSS VDD 372 591 423 DFFHQNx1_ASAP7_75t_R $T=49464 12960 1 0 $X=49464 $Y=11792
X4376 VSS VDD 381 631 466 DFFHQNx1_ASAP7_75t_R $T=50544 30240 0 0 $X=50544 $Y=30152
X4377 VSS VDD 48 450 478 DFFHQNx1_ASAP7_75t_R $T=51624 32400 1 0 $X=51624 $Y=31232
X4378 VSS VDD 372 595 457 DFFHQNx1_ASAP7_75t_R $T=51840 17280 1 0 $X=51840 $Y=16112
X4379 VSS VDD 381 596 463 DFFHQNx1_ASAP7_75t_R $T=51840 25920 1 0 $X=51840 $Y=24752
X4380 VSS VDD 381 379 471 DFFHQNx1_ASAP7_75t_R $T=52056 28080 0 0 $X=52056 $Y=27992
X4381 VSS VDD 381 456 448 DFFHQNx1_ASAP7_75t_R $T=52272 21600 1 0 $X=52272 $Y=20432
X4382 VSS VDD 46 453 588 DFFHQNx1_ASAP7_75t_R $T=52272 51840 1 0 $X=52272 $Y=50672
X4383 VSS VDD 372 455 462 DFFHQNx1_ASAP7_75t_R $T=52920 12960 0 0 $X=52920 $Y=12872
X4384 VSS VDD 210 626 133 ICV_25 $T=12528 15120 1 0 $X=12528 $Y=13952
X4385 VSS VDD 92 117 501 ICV_25 $T=12744 28080 0 0 $X=12744 $Y=27992
X4386 VSS VDD 210 193 167 ICV_25 $T=22032 15120 1 0 $X=22032 $Y=13952
X4387 VSS VDD 372 559 320 ICV_25 $T=31968 6480 0 0 $X=31968 $Y=6392
X4388 VSS VDD 48 673 373 ICV_25 $T=38232 43200 0 0 $X=38232 $Y=43112
X4389 VSS VDD 48 593 429 ICV_25 $T=50112 38880 0 0 $X=50112 $Y=38792
X4390 VSS VDD 46 597 458 ICV_25 $T=50976 49680 1 0 $X=50976 $Y=48512
X4391 VSS VDD 381 465 452 ICV_25 $T=52920 23760 1 0 $X=52920 $Y=22592
X4647 VSS VDD 80 66 490 AND2x2_ASAP7_75t_R $T=6912 36720 0 0 $X=6912 $Y=36632
X4648 VSS VDD 95 166 103 AND2x2_ASAP7_75t_R $T=9288 34560 1 0 $X=9288 $Y=33392
X4649 VSS VDD 102 66 97 AND2x2_ASAP7_75t_R $T=10152 28080 1 0 $X=10152 $Y=26912
X4650 VSS VDD 109 66 114 AND2x2_ASAP7_75t_R $T=11664 17280 0 0 $X=11664 $Y=17192
X4651 VSS VDD 111 66 500 AND2x2_ASAP7_75t_R $T=11664 23760 1 0 $X=11664 $Y=22592
X4652 VSS VDD 22 106 115 AND2x2_ASAP7_75t_R $T=11880 30240 1 0 $X=11880 $Y=29072
X4653 VSS VDD 123 501 17 AND2x2_ASAP7_75t_R $T=14256 28080 1 0 $X=14256 $Y=26912
X4654 VSS VDD 58 143 503 AND2x2_ASAP7_75t_R $T=14904 32400 0 0 $X=14904 $Y=32312
X4655 VSS VDD 143 496 159 AND2x2_ASAP7_75t_R $T=17928 19440 0 0 $X=17928 $Y=19352
X4656 VSS VDD 17 22 632 AND2x2_ASAP7_75t_R $T=18144 30240 1 0 $X=18144 $Y=29072
X4657 VSS VDD 58 99 184 AND2x2_ASAP7_75t_R $T=19440 23760 0 0 $X=19440 $Y=23672
X4658 VSS VDD 157 178 15 AND2x2_ASAP7_75t_R $T=20520 28080 1 0 $X=20520 $Y=26912
X4659 VSS VDD 190 513 162 AND2x2_ASAP7_75t_R $T=21600 17280 1 0 $X=21600 $Y=16112
X4660 VSS VDD 248 12 190 AND2x2_ASAP7_75t_R $T=23328 19440 0 0 $X=23328 $Y=19352
X4661 VSS VDD 139 24 220 AND2x2_ASAP7_75t_R $T=24408 32400 0 0 $X=24408 $Y=32312
X4662 VSS VDD 277 362 682 AND2x2_ASAP7_75t_R $T=33264 49680 1 0 $X=33264 $Y=48512
X4663 VSS VDD 304 330 317 AND2x2_ASAP7_75t_R $T=34776 38880 0 0 $X=34776 $Y=38792
X4664 VSS VDD 258 328 362 AND2x2_ASAP7_75t_R $T=35640 34560 0 0 $X=35640 $Y=34472
X4665 VSS VDD 316 322 562 AND2x2_ASAP7_75t_R $T=36072 41040 1 0 $X=36072 $Y=39872
X4666 VSS VDD 344 336 360 AND2x2_ASAP7_75t_R $T=39312 32400 1 0 $X=39312 $Y=31232
X4667 VSS VDD 643 66 644 AND2x2_ASAP7_75t_R $T=39744 21600 1 0 $X=39744 $Y=20432
X4668 VSS VDD 346 614 40 AND2x2_ASAP7_75t_R $T=41472 47520 1 0 $X=41472 $Y=46352
X4669 VSS VDD 370 66 572 AND2x2_ASAP7_75t_R $T=42336 49680 0 0 $X=42336 $Y=49592
X4670 VSS VDD 390 386 408 AND2x2_ASAP7_75t_R $T=45144 49680 1 0 $X=45144 $Y=48512
X4671 VSS VDD 416 616 665 AND2x2_ASAP7_75t_R $T=45792 43200 1 0 $X=45792 $Y=42032
X4672 VSS VDD 578 424 398 AND2x2_ASAP7_75t_R $T=46008 47520 1 0 $X=46008 $Y=46352
X4673 VSS VDD 409 66 648 AND2x2_ASAP7_75t_R $T=46872 34560 1 0 $X=46872 $Y=33392
X4674 VSS VDD 414 420 587 AND2x2_ASAP7_75t_R $T=47952 36720 1 0 $X=47952 $Y=35552
X4675 VSS VDD 446 66 450 AND2x2_ASAP7_75t_R $T=50328 32400 1 0 $X=50328 $Y=31232
X4676 VSS VDD 587 416 435 AND2x2_ASAP7_75t_R $T=50328 43200 0 0 $X=50328 $Y=43112
X4677 VSS VDD 649 66 453 AND2x2_ASAP7_75t_R $T=50976 51840 1 0 $X=50976 $Y=50672
X4678 VSS VDD 449 322 651 AND2x2_ASAP7_75t_R $T=51624 45360 1 0 $X=51624 $Y=44192
X4679 VSS VDD 451 66 597 AND2x2_ASAP7_75t_R $T=51840 47520 1 0 $X=51840 $Y=46352
X4680 VSS VDD ICV_36 $T=4752 12960 0 0 $X=4752 $Y=12872
X4681 VSS VDD ICV_36 $T=4752 15120 1 0 $X=4752 $Y=13952
X4682 VSS VDD ICV_36 $T=4752 15120 0 0 $X=4752 $Y=15032
X4683 VSS VDD ICV_36 $T=4752 47520 0 0 $X=4752 $Y=47432
X4684 VSS VDD ICV_36 $T=14256 51840 0 0 $X=14256 $Y=51752
X4685 VSS VDD ICV_36 $T=14256 54000 0 0 $X=14256 $Y=53912
X4686 VSS VDD ICV_36 $T=14256 56160 1 0 $X=14256 $Y=54992
X4687 VSS VDD ICV_36 $T=16200 32400 0 0 $X=16200 $Y=32312
X4688 VSS VDD ICV_36 $T=18576 10800 0 0 $X=18576 $Y=10712
X4689 VSS VDD ICV_36 $T=22680 25920 1 0 $X=22680 $Y=24752
X4690 VSS VDD ICV_36 $T=23544 10800 1 0 $X=23544 $Y=9632
X4691 VSS VDD ICV_36 $T=23760 58320 0 0 $X=23760 $Y=58232
X4692 VSS VDD ICV_36 $T=27648 43200 0 0 $X=27648 $Y=43112
X4693 VSS VDD ICV_36 $T=29592 36720 1 0 $X=29592 $Y=35552
X4694 VSS VDD ICV_36 $T=32400 32400 0 0 $X=32400 $Y=32312
X4695 VSS VDD ICV_36 $T=33480 17280 1 0 $X=33480 $Y=16112
X4696 VSS VDD ICV_36 $T=34992 10800 1 0 $X=34992 $Y=9632
X4697 VSS VDD ICV_36 $T=39960 30240 0 0 $X=39960 $Y=30152
X4698 VSS VDD ICV_36 $T=50976 15120 0 0 $X=50976 $Y=15032
X4699 VSS VDD ICV_36 $T=51192 32400 0 0 $X=51192 $Y=32312
X4700 VSS VDD ICV_36 $T=51192 49680 0 0 $X=51192 $Y=49592
X4701 VSS VDD ICV_36 $T=51408 34560 0 0 $X=51408 $Y=34472
X4702 VSS VDD ICV_36 $T=51408 58320 1 0 $X=51408 $Y=57152
X4703 VSS VDD ICV_36 $T=51840 4320 0 0 $X=51840 $Y=4232
X4704 VSS VDD ICV_36 $T=51840 8640 0 0 $X=51840 $Y=8552
X4705 VSS VDD ICV_36 $T=51840 41040 1 0 $X=51840 $Y=39872
X4706 VSS VDD 90 87 95 XNOR2x2_ASAP7_75t_R $T=6912 34560 1 0 $X=6912 $Y=33392
X4707 VSS VDD 82 107 658 XNOR2x2_ASAP7_75t_R $T=7128 41040 0 0 $X=7128 $Y=40786
X4708 VSS VDD 79 494 599 XNOR2x2_ASAP7_75t_R $T=7560 47520 1 0 $X=7560 $Y=46352
X4709 VSS VDD 104 81 116 XNOR2x2_ASAP7_75t_R $T=10800 43200 0 0 $X=10800 $Y=42946
X4710 VSS VDD 123 121 122 XNOR2x2_ASAP7_75t_R $T=12096 25920 0 0 $X=12096 $Y=25666
X4711 VSS VDD 75 502 652 XNOR2x2_ASAP7_75t_R $T=12096 47520 1 0 $X=12096 $Y=46352
X4712 VSS VDD 75 130 329 XNOR2x2_ASAP7_75t_R $T=13824 45360 0 0 $X=13824 $Y=45106
X4713 VSS VDD 174 512 186 XNOR2x2_ASAP7_75t_R $T=18360 49680 0 0 $X=18360 $Y=49426
X4714 VSS VDD 192 185 659 XNOR2x2_ASAP7_75t_R $T=20736 49680 1 0 $X=20736 $Y=48512
X4715 VSS VDD 197 216 224 XNOR2x2_ASAP7_75t_R $T=24192 56160 1 0 $X=24192 $Y=54992
X4716 VSS VDD 232 534 222 XNOR2x2_ASAP7_75t_R $T=25272 43200 0 0 $X=25272 $Y=42946
X4717 VSS VDD 34 663 661 XNOR2x2_ASAP7_75t_R $T=26352 45360 1 0 $X=26352 $Y=44192
X4718 VSS VDD 278 540 245 XNOR2x2_ASAP7_75t_R $T=26352 47520 1 0 $X=26352 $Y=46352
X4719 VSS VDD 192 535 246 XNOR2x2_ASAP7_75t_R $T=26568 51840 0 0 $X=26568 $Y=51586
X4720 VSS VDD 247 243 253 XNOR2x2_ASAP7_75t_R $T=27216 36720 1 0 $X=27216 $Y=35552
X4721 VSS VDD 174 30 254 XNOR2x2_ASAP7_75t_R $T=27216 49680 0 0 $X=27216 $Y=49426
X4722 VSS VDD 259 118 264 XNOR2x2_ASAP7_75t_R $T=28080 47520 0 0 $X=28080 $Y=47266
X4723 VSS VDD 280 231 548 XNOR2x2_ASAP7_75t_R $T=28296 51840 1 0 $X=28296 $Y=50672
X4724 VSS VDD 232 62 296 XNOR2x2_ASAP7_75t_R $T=30456 45360 0 0 $X=30456 $Y=45106
X4725 VSS VDD 278 235 639 XNOR2x2_ASAP7_75t_R $T=30672 51840 1 0 $X=30672 $Y=50672
X4726 VSS VDD 357 406 313 XNOR2x2_ASAP7_75t_R $T=39960 51840 1 0 $X=39960 $Y=50672
X4727 VSS VDD 355 368 304 XNOR2x2_ASAP7_75t_R $T=40392 38880 1 0 $X=40392 $Y=37712
X4728 VSS VDD 371 568 42 XNOR2x2_ASAP7_75t_R $T=40824 36720 0 0 $X=40824 $Y=36466
X4729 VSS VDD 569 402 335 XNOR2x2_ASAP7_75t_R $T=41472 56160 1 0 $X=41472 $Y=54992
X4730 VSS VDD 376 654 399 XNOR2x2_ASAP7_75t_R $T=43200 54000 1 0 $X=43200 $Y=52832
X4731 VSS VDD 373 405 404 XNOR2x2_ASAP7_75t_R $T=43632 45360 0 0 $X=43632 $Y=45106
X4732 VSS VDD 421 585 425 XNOR2x2_ASAP7_75t_R $T=46008 51840 0 0 $X=46008 $Y=51586
X4733 VSS VDD 436 422 443 XNOR2x2_ASAP7_75t_R $T=47520 56160 1 0 $X=47520 $Y=54992
X4734 VSS VDD 588 618 680 XNOR2x2_ASAP7_75t_R $T=48600 49680 1 0 $X=48600 $Y=48512
X4735 VSS VDD 430 650 657 XNOR2x2_ASAP7_75t_R $T=50544 43200 1 0 $X=50544 $Y=42032
X4736 VSS VDD 458 447 461 XNOR2x2_ASAP7_75t_R $T=51624 47520 0 0 $X=51624 $Y=47266
X4737 VSS VDD 92 74 634 ICV_37 $T=4752 23760 0 0 $X=4752 $Y=23672
X4738 VSS VDD 32 203 26 ICV_37 $T=22464 36720 0 0 $X=22464 $Y=36632
X4739 VSS VDD 210 204 218 ICV_37 $T=22680 8640 1 0 $X=22680 $Y=7472
X4740 VSS VDD 210 675 248 ICV_37 $T=24624 19440 0 0 $X=24624 $Y=19352
X4741 VSS VDD 372 265 256 ICV_37 $T=29376 17280 0 0 $X=29376 $Y=17192
X4742 VSS VDD 32 562 355 ICV_37 $T=35856 38880 1 0 $X=35856 $Y=37712
X4743 VSS VDD 46 683 380 ICV_37 $T=38880 58320 0 0 $X=38880 $Y=58232
X4744 VSS VDD 32 645 382 ICV_37 $T=39096 38880 0 0 $X=39096 $Y=38792
X4745 VSS VDD 372 681 459 ICV_37 $T=49032 15120 1 0 $X=49032 $Y=13952
X4746 VSS VDD 48 651 430 ICV_37 $T=51624 43200 0 0 $X=51624 $Y=43112
X4747 VSS VDD 125 493 634 491 AND3x1_ASAP7_75t_R $T=9288 30240 1 0 $X=9288 $Y=29072
X4748 VSS VDD 634 94 106 87 AND3x1_ASAP7_75t_R $T=9288 30240 0 0 $X=9288 $Y=30030
X4749 VSS VDD 94 90 491 108 AND3x1_ASAP7_75t_R $T=10152 32400 0 0 $X=10152 $Y=32190
X4750 VSS VDD 496 143 101 622 AND3x1_ASAP7_75t_R $T=10368 38880 1 0 $X=10368 $Y=37712
X4751 VSS VDD 106 22 17 139 AND3x1_ASAP7_75t_R $T=15552 28080 1 0 $X=15552 $Y=26912
X4752 VSS VDD 6 137 140 506 AND3x1_ASAP7_75t_R $T=15984 45360 1 0 $X=15984 $Y=44192
X4753 VSS VDD 602 163 162 149 AND3x1_ASAP7_75t_R $T=16632 12960 1 0 $X=16632 $Y=11792
X4754 VSS VDD 168 513 190 99 AND3x1_ASAP7_75t_R $T=19440 19440 1 0 $X=19440 $Y=18272
X4755 VSS VDD 181 496 16 660 AND3x1_ASAP7_75t_R $T=20088 17280 0 0 $X=20088 $Y=17070
X4756 VSS VDD 29 23 135 195 AND3x1_ASAP7_75t_R $T=21168 36720 0 0 $X=21168 $Y=36510
X4757 VSS VDD 607 167 248 199 AND3x1_ASAP7_75t_R $T=22680 19440 1 0 $X=22680 $Y=18272
X4758 VSS VDD 194 207 190 107 AND3x1_ASAP7_75t_R $T=23544 17280 0 0 $X=23544 $Y=17070
X4759 VSS VDD 234 24 135 243 AND3x1_ASAP7_75t_R $T=26352 34560 1 0 $X=26352 $Y=33392
X4760 VSS VDD 66 496 143 662 AND3x1_ASAP7_75t_R $T=27000 38880 0 0 $X=27000 $Y=38670
X4761 VSS VDD 234 247 220 270 AND3x1_ASAP7_75t_R $T=28080 32400 0 0 $X=28080 $Y=32190
X4762 VSS VDD 547 554 252 230 AND3x1_ASAP7_75t_R $T=30888 34560 0 0 $X=30888 $Y=34350
X4763 VSS VDD 274 328 258 279 AND3x1_ASAP7_75t_R $T=31320 41040 1 0 $X=31320 $Y=39872
X4764 VSS VDD 66 614 346 349 AND3x1_ASAP7_75t_R $T=38664 51840 1 0 $X=38664 $Y=50672
X4765 VSS VDD 307 321 336 359 AND3x1_ASAP7_75t_R $T=39312 30240 1 0 $X=39312 $Y=29072
X4766 VSS VDD 420 414 382 568 AND3x1_ASAP7_75t_R $T=42984 36720 1 0 $X=42984 $Y=35552
X4767 VSS VDD 569 380 402 654 AND3x1_ASAP7_75t_R $T=42984 54000 0 0 $X=42984 $Y=53790
X4768 VSS VDD 382 371 616 368 AND3x1_ASAP7_75t_R $T=43632 38880 0 0 $X=43632 $Y=38670
X4769 VSS VDD 66 417 684 403 AND3x1_ASAP7_75t_R $T=44496 28080 1 0 $X=44496 $Y=26912
X4770 VSS VDD 616 416 424 390 AND3x1_ASAP7_75t_R $T=44928 43200 0 0 $X=44928 $Y=42990
X4771 VSS VDD 386 421 390 422 AND3x1_ASAP7_75t_R $T=46872 54000 0 0 $X=46872 $Y=53790
X4772 VSS VDD 386 424 435 585 AND3x1_ASAP7_75t_R $T=47304 49680 1 0 $X=47304 $Y=48512
X4773 VSS VDD 429 430 665 447 AND3x1_ASAP7_75t_R $T=49680 47520 0 0 $X=49680 $Y=47310
X4774 VSS VDD 66 592 647 593 AND3x1_ASAP7_75t_R $T=50544 41040 1 0 $X=50544 $Y=39872
X4775 VSS VDD 429 416 587 650 AND3x1_ASAP7_75t_R $T=51408 41040 0 0 $X=51408 $Y=40830
X4776 VSS VDD 100 58 93 66 74 OA211x2_ASAP7_75t_R $T=9936 23760 1 0 $X=9936 $Y=22592
X4777 VSS VDD 134 7 126 151 128 OA211x2_ASAP7_75t_R $T=16200 36720 1 0 $X=16200 $Y=35552
X4778 VSS VDD 603 506 669 66 131 OA211x2_ASAP7_75t_R $T=16200 45360 0 0 $X=16200 $Y=45272
X4779 VSS VDD 604 496 137 4 175 OA211x2_ASAP7_75t_R $T=18360 43200 0 0 $X=18360 $Y=43112
X4780 VSS VDD 176 628 511 66 635 OA211x2_ASAP7_75t_R $T=18792 34560 0 0 $X=18792 $Y=34472
X4781 VSS VDD 172 171 158 66 182 OA211x2_ASAP7_75t_R $T=18792 41040 1 0 $X=18792 $Y=39872
X4782 VSS VDD 159 202 524 66 180 OA211x2_ASAP7_75t_R $T=23760 12960 0 0 $X=23760 $Y=12872
X4783 VSS VDD 215 496 509 234 228 OA211x2_ASAP7_75t_R $T=25272 30240 1 0 $X=25272 $Y=29072
X4784 VSS VDD 159 237 533 66 233 OA211x2_ASAP7_75t_R $T=25488 12960 0 0 $X=25488 $Y=12872
X4785 VSS VDD 294 324 286 66 284 OA211x2_ASAP7_75t_R $T=32400 23760 0 0 $X=32400 $Y=23672
X4786 VSS VDD 313 305 309 322 564 OA211x2_ASAP7_75t_R $T=35424 51840 1 0 $X=35424 $Y=50672
X4787 VSS VDD 329 258 679 614 343 OA211x2_ASAP7_75t_R $T=37152 45360 1 0 $X=37152 $Y=44192
X4788 VSS VDD 335 614 315 322 345 OA211x2_ASAP7_75t_R $T=37584 56160 1 0 $X=37584 $Y=54992
X4789 VSS VDD 387 305 341 66 645 OA211x2_ASAP7_75t_R $T=38664 41040 1 0 $X=38664 $Y=39872
X4790 VSS VDD 360 340 614 420 367 OA211x2_ASAP7_75t_R $T=40176 32400 0 0 $X=40176 $Y=32312
X4791 VSS VDD 364 366 623 66 354 OA211x2_ASAP7_75t_R $T=41256 12960 1 0 $X=41256 $Y=11792
X4792 VSS VDD 364 392 667 66 576 OA211x2_ASAP7_75t_R $T=44496 12960 1 0 $X=44496 $Y=11792
X4793 VSS VDD 388 573 579 322 670 OA211x2_ASAP7_75t_R $T=45144 56160 0 0 $X=45144 $Y=56072
X4794 VSS VDD 443 614 314 322 655 OA211x2_ASAP7_75t_R $T=46872 56160 0 0 $X=46872 $Y=56072
X4795 VSS VDD 425 614 310 322 431 OA211x2_ASAP7_75t_R $T=47088 54000 1 0 $X=47088 $Y=52832
X4796 VSS VDD 364 411 656 66 591 OA211x2_ASAP7_75t_R $T=48816 12960 0 0 $X=48816 $Y=12872
X4797 VSS VDD 364 438 467 66 455 OA211x2_ASAP7_75t_R $T=51192 12960 0 0 $X=51192 $Y=12872
X4798 VSS VDD 364 352 594 66 456 OA211x2_ASAP7_75t_R $T=51192 19440 0 0 $X=51192 $Y=19352
X4799 VSS VDD 364 400 668 66 465 OA211x2_ASAP7_75t_R $T=51192 23760 1 0 $X=51192 $Y=22592
X4800 VSS VDD 364 428 464 66 595 OA211x2_ASAP7_75t_R $T=51408 17280 0 0 $X=51408 $Y=17192
X4801 VSS VDD 364 426 468 66 596 OA211x2_ASAP7_75t_R $T=51408 23760 0 0 $X=51408 $Y=23672
X5040 VSS VDD 159 658 492 78 AO21x1_ASAP7_75t_R $T=8424 32400 0 0 $X=8424 $Y=32238
X5041 VSS VDD 88 107 98 101 AO21x1_ASAP7_75t_R $T=8856 38880 0 0 $X=8856 $Y=38718
X5042 VSS VDD 125 166 499 109 AO21x1_ASAP7_75t_R $T=10368 17280 0 0 $X=10368 $Y=17118
X5043 VSS VDD 685 166 498 111 AO21x1_ASAP7_75t_R $T=10584 21600 0 0 $X=10584 $Y=21438
X5044 VSS VDD 7 135 496 140 AO21x1_ASAP7_75t_R $T=15768 43200 1 0 $X=15768 $Y=42032
X5045 VSS VDD 166 139 148 151 AO21x1_ASAP7_75t_R $T=16848 34560 0 0 $X=16848 $Y=34398
X5046 VSS VDD 133 16 149 156 AO21x1_ASAP7_75t_R $T=17280 15120 1 0 $X=17280 $Y=13952
X5047 VSS VDD 507 162 508 160 AO21x1_ASAP7_75t_R $T=17712 8640 0 0 $X=17712 $Y=8478
X5048 VSS VDD 159 186 146 172 AO21x1_ASAP7_75t_R $T=18576 38880 1 0 $X=18576 $Y=37712
X5049 VSS VDD 196 162 676 189 AO21x1_ASAP7_75t_R $T=20520 8640 0 0 $X=20520 $Y=8478
X5050 VSS VDD 12 629 636 14 AO21x1_ASAP7_75t_R $T=20952 21600 1 0 $X=20952 $Y=20432
X5051 VSS VDD 521 162 523 213 AO21x1_ASAP7_75t_R $T=23328 6480 1 0 $X=23328 $Y=5312
X5052 VSS VDD 143 222 166 226 AO21x1_ASAP7_75t_R $T=25704 30240 0 0 $X=25704 $Y=30078
X5053 VSS VDD 143 661 166 236 AO21x1_ASAP7_75t_R $T=26352 41040 0 0 $X=26352 $Y=40878
X5054 VSS VDD 536 162 537 238 AO21x1_ASAP7_75t_R $T=26784 6480 1 0 $X=26784 $Y=5312
X5055 VSS VDD 143 548 166 252 AO21x1_ASAP7_75t_R $T=28296 38880 0 0 $X=28296 $Y=38718
X5056 VSS VDD 546 162 269 272 AO21x1_ASAP7_75t_R $T=30456 15120 1 0 $X=30456 $Y=13952
X5057 VSS VDD 550 162 638 276 AO21x1_ASAP7_75t_R $T=30888 6480 1 0 $X=30888 $Y=5312
X5058 VSS VDD 274 348 601 41 AO21x1_ASAP7_75t_R $T=31536 38880 1 0 $X=31536 $Y=37712
X5059 VSS VDD 556 162 557 289 AO21x1_ASAP7_75t_R $T=32400 10800 1 0 $X=32400 $Y=9632
X5060 VSS VDD 308 362 330 298 AO21x1_ASAP7_75t_R $T=33264 32400 1 0 $X=33264 $Y=31232
X5061 VSS VDD 287 162 297 299 AO21x1_ASAP7_75t_R $T=33480 8640 1 0 $X=33480 $Y=7472
X5062 VSS VDD 348 116 279 300 AO21x1_ASAP7_75t_R $T=33480 41040 1 0 $X=33480 $Y=39872
X5063 VSS VDD 305 300 317 316 AO21x1_ASAP7_75t_R $T=34776 41040 1 0 $X=34776 $Y=39872
X5064 VSS VDD 358 565 331 328 AO21x1_ASAP7_75t_R $T=36720 25920 0 0 $X=36720 $Y=25758
X5065 VSS VDD 346 136 330 341 AO21x1_ASAP7_75t_R $T=37368 41040 1 0 $X=37368 $Y=39872
X5066 VSS VDD 307 565 672 344 AO21x1_ASAP7_75t_R $T=38232 28080 1 0 $X=38232 $Y=26912
X5067 VSS VDD 358 565 350 363 AO21x1_ASAP7_75t_R $T=40176 23760 0 0 $X=40176 $Y=23598
X5068 VSS VDD 617 330 397 409 AO21x1_ASAP7_75t_R $T=45360 34560 1 0 $X=45360 $Y=33392
X5069 VSS VDD 348 614 413 417 AO21x1_ASAP7_75t_R $T=45792 28080 1 0 $X=45792 $Y=26912
X5070 VSS VDD 43 657 343 449 AO21x1_ASAP7_75t_R $T=50328 45360 1 0 $X=50328 $Y=44192
X5080 VSS VDD ICV_48 $T=4752 17280 1 0 $X=4752 $Y=16112
X5081 VSS VDD ICV_48 $T=4752 21600 1 0 $X=4752 $Y=20432
X5082 VSS VDD ICV_48 $T=4752 49680 1 0 $X=4752 $Y=48512
X5083 VSS VDD ICV_48 $T=10584 32400 1 0 $X=10584 $Y=31232
X5084 VSS VDD ICV_48 $T=11232 25920 1 0 $X=11232 $Y=24752
X5085 VSS VDD ICV_48 $T=14256 54000 1 0 $X=14256 $Y=52832
X5086 VSS VDD ICV_48 $T=19008 43200 1 0 $X=19008 $Y=42032
X5087 VSS VDD ICV_48 $T=26784 15120 0 0 $X=26784 $Y=15032
X5088 VSS VDD ICV_48 $T=27216 21600 1 0 $X=27216 $Y=20432
X5089 VSS VDD ICV_48 $T=29160 19440 0 0 $X=29160 $Y=19352
X5090 VSS VDD ICV_48 $T=30024 60480 1 0 $X=30024 $Y=59312
X5091 VSS VDD ICV_48 $T=30888 34560 1 0 $X=30888 $Y=33392
X5092 VSS VDD ICV_48 $T=32832 4320 0 0 $X=32832 $Y=4232
X5093 VSS VDD ICV_48 $T=35424 6480 1 0 $X=35424 $Y=5312
X5094 VSS VDD ICV_48 $T=36720 6480 0 0 $X=36720 $Y=6392
X5095 VSS VDD ICV_48 $T=39528 60480 1 0 $X=39528 $Y=59312
X5096 VSS VDD ICV_48 $T=39960 28080 0 0 $X=39960 $Y=27992
X5097 VSS VDD ICV_48 $T=42336 4320 0 0 $X=42336 $Y=4232
X5098 VSS VDD ICV_48 $T=42336 8640 0 0 $X=42336 $Y=8552
X5099 VSS VDD ICV_48 $T=43416 30240 1 0 $X=43416 $Y=29072
X5100 VSS VDD ICV_48 $T=43416 58320 0 0 $X=43416 $Y=58232
X5101 VSS VDD ICV_48 $T=46008 45360 0 0 $X=46008 $Y=45272
X5102 VSS VDD ICV_48 $T=46224 6480 0 0 $X=46224 $Y=6392
X5103 VSS VDD ICV_48 $T=46440 10800 0 0 $X=46440 $Y=10712
X5104 VSS VDD ICV_48 $T=46656 38880 1 0 $X=46656 $Y=37712
X5105 VSS VDD ICV_48 $T=46872 8640 1 0 $X=46872 $Y=7472
X5106 VSS VDD ICV_48 $T=47088 28080 1 0 $X=47088 $Y=26912
X5107 VSS VDD ICV_48 $T=47520 6480 1 0 $X=47520 $Y=5312
X5108 VSS VDD ICV_48 $T=48168 34560 1 0 $X=48168 $Y=33392
X5109 VSS VDD ICV_48 $T=48384 51840 0 0 $X=48384 $Y=51752
X5110 VSS VDD ICV_48 $T=48600 10800 1 0 $X=48600 $Y=9632
X5111 VSS VDD ICV_48 $T=48600 56160 0 0 $X=48600 $Y=56072
X5112 VSS VDD ICV_48 $T=49032 60480 1 0 $X=49032 $Y=59312
X5113 VSS VDD ICV_48 $T=49248 36720 1 0 $X=49248 $Y=35552
X5114 VSS VDD ICV_48 $T=49248 36720 0 0 $X=49248 $Y=36632
X5115 VSS VDD ICV_48 $T=49680 54000 0 0 $X=49680 $Y=53912
X5116 VSS VDD ICV_48 $T=49896 56160 1 0 $X=49896 $Y=54992
X5117 VSS VDD 166 122 159 599 102 AO22x1_ASAP7_75t_R $T=8208 28080 1 0 $X=8208 $Y=26912
X5118 VSS VDD 330 435 333 40 401 AO22x1_ASAP7_75t_R $T=43848 41040 0 0 $X=43848 $Y=40862
X5119 VSS VDD 40 301 330 680 649 AO22x1_ASAP7_75t_R $T=49248 49680 0 0 $X=49248 $Y=49502
X5120 VSS VDD 330 461 40 641 451 AO22x1_ASAP7_75t_R $T=49896 47520 1 0 $X=49896 $Y=46352
X5144 78 VSS VDD 85 66 71 OA21x2_ASAP7_75t_R $T=6264 32400 0 0 $X=6264 $Y=32312
X5145 103 VSS VDD 622 66 72 OA21x2_ASAP7_75t_R $T=10800 36720 1 0 $X=10800 $Y=35552
X5146 129 VSS VDD 258 328 136 OA21x2_ASAP7_75t_R $T=15120 41040 1 0 $X=15120 $Y=39872
X5147 107 VSS VDD 118 259 142 OA21x2_ASAP7_75t_R $T=15336 49680 1 0 $X=15336 $Y=48512
X5148 166 VSS VDD 16 164 152 OA21x2_ASAP7_75t_R $T=16632 21600 1 0 $X=16632 $Y=20432
X5149 188 VSS VDD 160 666 150 OA21x2_ASAP7_75t_R $T=19008 8640 0 0 $X=19008 $Y=8552
X5150 10 VSS VDD 659 66 671 OA21x2_ASAP7_75t_R $T=20304 47520 0 0 $X=20304 $Y=47432
X5151 179 VSS VDD 660 66 193 OA21x2_ASAP7_75t_R $T=20520 15120 1 0 $X=20520 $Y=13952
X5152 175 VSS VDD 170 671 516 OA21x2_ASAP7_75t_R $T=20520 45360 0 0 $X=20520 $Y=45272
X5153 188 VSS VDD 189 520 183 OA21x2_ASAP7_75t_R $T=21168 8640 1 0 $X=21168 $Y=7472
X5154 184 VSS VDD 201 26 211 OA21x2_ASAP7_75t_R $T=22896 34560 1 0 $X=22896 $Y=33392
X5155 188 VSS VDD 213 223 204 OA21x2_ASAP7_75t_R $T=23976 6480 0 0 $X=23976 $Y=6392
X5156 206 VSS VDD 215 166 526 OA21x2_ASAP7_75t_R $T=23976 28080 1 0 $X=23976 $Y=26912
X5157 209 VSS VDD 518 256 529 OA21x2_ASAP7_75t_R $T=24840 17280 0 0 $X=24840 $Y=17192
X5158 208 VSS VDD 205 66 675 OA21x2_ASAP7_75t_R $T=25704 21600 1 0 $X=25704 $Y=20432
X5159 188 VSS VDD 238 241 240 OA21x2_ASAP7_75t_R $T=27648 6480 0 0 $X=27648 $Y=6392
X5160 188 VSS VDD 272 266 265 OA21x2_ASAP7_75t_R $T=30888 17280 1 0 $X=30888 $Y=16112
X5161 188 VSS VDD 276 311 559 OA21x2_ASAP7_75t_R $T=32616 6480 1 0 $X=32616 $Y=5312
X5162 283 VSS VDD 348 328 294 OA21x2_ASAP7_75t_R $T=32616 25920 0 0 $X=32616 $Y=25832
X5163 188 VSS VDD 289 613 302 OA21x2_ASAP7_75t_R $T=33480 12960 1 0 $X=33480 $Y=11792
X5164 188 VSS VDD 299 561 560 OA21x2_ASAP7_75t_R $T=33912 8640 0 0 $X=33912 $Y=8552
X5165 326 VSS VDD 307 325 678 OA21x2_ASAP7_75t_R $T=37152 30240 1 0 $X=37152 $Y=29072
X5166 337 VSS VDD 334 614 353 OA21x2_ASAP7_75t_R $T=38016 25920 1 0 $X=38016 $Y=24752
X5167 43 VSS VDD 306 356 673 OA21x2_ASAP7_75t_R $T=38880 45360 1 0 $X=38880 $Y=44192
X5168 305 VSS VDD 404 322 356 OA21x2_ASAP7_75t_R $T=38880 45360 0 0 $X=38880 $Y=45272
X5169 330 VSS VDD 346 66 322 OA21x2_ASAP7_75t_R $T=39312 47520 0 0 $X=39312 $Y=47432
X5170 682 VSS VDD 563 670 646 OA21x2_ASAP7_75t_R $T=39312 56160 1 0 $X=39312 $Y=54992
X5171 352 VSS VDD 374 375 566 OA21x2_ASAP7_75t_R $T=39960 19440 0 0 $X=39960 $Y=19352
X5172 362 VSS VDD 360 614 369 OA21x2_ASAP7_75t_R $T=40824 32400 1 0 $X=40824 $Y=31232
X5173 366 VSS VDD 374 615 361 OA21x2_ASAP7_75t_R $T=41040 15120 1 0 $X=41040 $Y=13952
X5174 420 VSS VDD 369 571 384 OA21x2_ASAP7_75t_R $T=42336 32400 1 0 $X=42336 $Y=31232
X5175 392 VSS VDD 374 624 377 OA21x2_ASAP7_75t_R $T=44496 15120 1 0 $X=44496 $Y=13952
X5176 385 VSS VDD 401 577 581 OA21x2_ASAP7_75t_R $T=45144 38880 1 0 $X=45144 $Y=37712
X5177 400 VSS VDD 374 583 418 OA21x2_ASAP7_75t_R $T=45576 21600 0 0 $X=45576 $Y=21512
X5178 411 VSS VDD 374 625 412 OA21x2_ASAP7_75t_R $T=46440 17280 1 0 $X=46440 $Y=16112
X5179 426 VSS VDD 374 433 407 OA21x2_ASAP7_75t_R $T=48384 25920 1 0 $X=48384 $Y=24752
X5180 428 VSS VDD 374 437 589 OA21x2_ASAP7_75t_R $T=48600 19440 0 0 $X=48600 $Y=19352
X5181 438 VSS VDD 374 432 681 OA21x2_ASAP7_75t_R $T=49464 15120 0 0 $X=49464 $Y=15032
X5190 VSS VDD 503 112 21 119 115 132 AO221x1_ASAP7_75t_R $T=13824 30240 0 0 $X=13824 $Y=30010
X5191 VSS VDD 605 503 21 517 195 637 AO221x1_ASAP7_75t_R $T=22464 38880 1 0 $X=22464 $Y=37712
X5192 VSS VDD 503 221 21 538 220 229 AO221x1_ASAP7_75t_R $T=24840 32400 1 0 $X=24840 $Y=31232
X5193 VSS VDD 686 362 330 348 277 309 AO221x1_ASAP7_75t_R $T=33048 49680 0 0 $X=33048 $Y=49450
X5194 VSS VDD 246 362 330 348 261 310 AO221x1_ASAP7_75t_R $T=33048 54000 1 0 $X=33048 $Y=52832
X5195 VSS VDD 261 362 330 348 224 314 AO221x1_ASAP7_75t_R $T=33480 54000 0 0 $X=33480 $Y=53770
X5196 VSS VDD 224 362 43 348 531 315 AO221x1_ASAP7_75t_R $T=33696 56160 1 0 $X=33696 $Y=54992
X5197 VSS VDD 531 362 330 348 639 318 AO221x1_ASAP7_75t_R $T=33912 56160 0 0 $X=33912 $Y=55930
X5198 VSS VDD 408 43 575 40 291 370 AO221x1_ASAP7_75t_R $T=41688 49680 1 0 $X=41688 $Y=48512
X5199 VSS VDD 40 347 445 43 665 592 AO221x1_ASAP7_75t_R $T=49248 41040 0 0 $X=49248 $Y=40810
X5222 VSS VDD 107 166 21 168 141 OR4x1_ASAP7_75t_R $T=16848 17280 0 0 $X=16848 $Y=17192
X5223 VSS VDD 107 162 166 21 147 OR4x1_ASAP7_75t_R $T=18144 17280 1 0 $X=18144 $Y=16112
X5224 VSS VDD 206 173 187 515 166 OR4x1_ASAP7_75t_R $T=19008 28080 0 0 $X=19008 $Y=27992
X5225 VSS VDD 30 107 192 174 687 OR4x1_ASAP7_75t_R $T=21384 49680 0 0 $X=21384 $Y=49592
X5226 VSS VDD 30 606 107 197 212 OR4x1_ASAP7_75t_R $T=23544 51840 0 0 $X=23544 $Y=51752
X5227 VSS VDD 30 31 107 34 534 OR4x1_ASAP7_75t_R $T=26352 45360 0 0 $X=26352 $Y=45272
X5228 VSS VDD 30 225 107 278 231 OR4x1_ASAP7_75t_R $T=26784 51840 1 0 $X=26784 $Y=50672
X5229 VSS VDD 324 340 308 285 282 OR4x1_ASAP7_75t_R $T=33480 28080 0 0 $X=33480 $Y=27992
X5230 VSS VDD 332 630 338 326 327 OR4x1_ASAP7_75t_R $T=37152 30240 0 0 $X=37152 $Y=30152
X5231 VSS VDD 614 439 383 429 647 OR4x1_ASAP7_75t_R $T=47088 38880 0 0 $X=47088 $Y=38792
X5232 VSS VDD 586 419 439 415 43 OR4x1_ASAP7_75t_R $T=48600 38880 0 0 $X=48600 $Y=38792
X5233 VSS VDD 586 419 439 415 330 OR4x1_ASAP7_75t_R $T=48600 41040 1 0 $X=48600 $Y=39872
X5234 VSS VDD 327 43 21 478 427 OR4x1_ASAP7_75t_R $T=49032 30240 0 0 $X=49032 $Y=30152
X5235 VSS VDD 58 99 107 93 OAI21x1_ASAP7_75t_R $T=8856 21600 0 0 $X=8856 $Y=21446
X5236 VSS VDD 137 496 115 127 OAI21x1_ASAP7_75t_R $T=14904 30240 1 0 $X=14904 $Y=29072
X5237 VSS VDD 139 166 143 134 OAI21x1_ASAP7_75t_R $T=16200 38880 1 0 $X=16200 $Y=37712
X5238 VSS VDD 263 340 296 306 OAI21x1_ASAP7_75t_R $T=33264 45360 1 0 $X=33264 $Y=44192
X5239 VSS VDD 614 258 296 563 OAI21x1_ASAP7_75t_R $T=35424 45360 1 0 $X=35424 $Y=44192
X5240 VSS VDD 258 337 89 679 OAI21x1_ASAP7_75t_R $T=36504 43200 0 0 $X=36504 $Y=43046
X5241 VSS VDD 614 332 678 324 OAI21x1_ASAP7_75t_R $T=36936 28080 0 0 $X=36936 $Y=27926
X5242 VSS VDD 323 258 89 347 OAI21x1_ASAP7_75t_R $T=37584 43200 1 0 $X=37584 $Y=42032
X5243 VSS VDD 374 326 348 375 OAI21x1_ASAP7_75t_R $T=41472 19440 0 0 $X=41472 $Y=19286
X5244 VSS VDD 374 378 348 615 OAI21x1_ASAP7_75t_R $T=41688 17280 0 0 $X=41688 $Y=17126
X5245 VSS VDD 374 396 348 624 OAI21x1_ASAP7_75t_R $T=44064 15120 0 0 $X=44064 $Y=14966
X5246 VSS VDD 374 410 348 625 OAI21x1_ASAP7_75t_R $T=46224 15120 0 0 $X=46224 $Y=14966
X5247 VSS VDD 374 441 348 583 OAI21x1_ASAP7_75t_R $T=46872 21600 1 0 $X=46872 $Y=20432
X5248 VSS VDD 374 444 348 437 OAI21x1_ASAP7_75t_R $T=48600 21600 1 0 $X=48600 $Y=20432
X5249 VSS VDD 374 442 348 433 OAI21x1_ASAP7_75t_R $T=48816 21600 0 0 $X=48816 $Y=21446
X5250 VSS VDD 374 454 348 432 OAI21x1_ASAP7_75t_R $T=49032 17280 1 0 $X=49032 $Y=16112
X5269 VSS VDD 497 107 98 NOR2x1_ASAP7_75t_R $T=10152 38880 0 0 $X=10152 $Y=38792
X5270 VSS VDD 107 130 502 NOR2x1_ASAP7_75t_R $T=12744 47520 0 0 $X=12744 $Y=47432
X5271 VSS VDD 154 162 508 NOR2x1_ASAP7_75t_R $T=17280 10800 0 0 $X=17280 $Y=10712
X5272 VSS VDD 4 604 170 NOR2x1_ASAP7_75t_R $T=18360 45360 1 0 $X=18360 $Y=44192
X5273 VSS VDD 107 169 138 NOR2x1_ASAP7_75t_R $T=18792 47520 0 0 $X=18792 $Y=47432
X5274 VSS VDD 167 159 179 NOR2x1_ASAP7_75t_R $T=19224 15120 1 0 $X=19224 $Y=13952
X5275 VSS VDD 177 162 676 NOR2x1_ASAP7_75t_R $T=20088 6480 1 0 $X=20088 $Y=5312
X5276 VSS VDD 58 195 201 NOR2x1_ASAP7_75t_R $T=21600 34560 1 0 $X=21600 $Y=33392
X5277 VSS VDD 633 162 523 NOR2x1_ASAP7_75t_R $T=22896 8640 0 0 $X=22896 $Y=8552
X5278 VSS VDD 211 637 203 NOR2x1_ASAP7_75t_R $T=23760 36720 1 0 $X=23760 $Y=35552
X5279 VSS VDD 218 162 537 NOR2x1_ASAP7_75t_R $T=26568 8640 0 0 $X=26568 $Y=8552
X5280 VSS VDD 228 229 541 NOR2x1_ASAP7_75t_R $T=27000 30240 1 0 $X=27000 $Y=29072
X5281 VSS VDD 251 162 638 NOR2x1_ASAP7_75t_R $T=30672 6480 0 0 $X=30672 $Y=6392
X5282 VSS VDD 275 162 269 NOR2x1_ASAP7_75t_R $T=31752 15120 1 0 $X=31752 $Y=13952
X5283 VSS VDD 290 162 557 NOR2x1_ASAP7_75t_R $T=32184 10800 0 0 $X=32184 $Y=10712
X5284 VSS VDD 320 162 297 NOR2x1_ASAP7_75t_R $T=34776 8640 1 0 $X=34776 $Y=7472
X5285 VSS VDD 21 65 342 NOR2x1_ASAP7_75t_R $T=37368 36720 1 0 $X=37368 $Y=35552
X5286 VSS VDD 307 332 348 NOR2x1_ASAP7_75t_R $T=38664 28080 0 0 $X=38664 $Y=27992
X5287 VSS VDD 326 307 672 NOR2x1_ASAP7_75t_R $T=39528 28080 1 0 $X=39528 $Y=26912
X5288 VSS VDD 21 367 571 NOR2x1_ASAP7_75t_R $T=42336 32400 0 0 $X=42336 $Y=32312
X5289 VSS VDD 386 390 575 NOR2x1_ASAP7_75t_R $T=43848 49680 1 0 $X=43848 $Y=48512
X5290 VSS VDD 21 389 577 NOR2x1_ASAP7_75t_R $T=44928 38880 0 0 $X=44928 $Y=38792
X5291 VSS VDD 419 327 440 NOR2x1_ASAP7_75t_R $T=48600 32400 0 0 $X=48600 $Y=32312
X5292 VSS VDD 94 166 491 492 ICV_54 $T=7776 30240 1 0 $X=7776 $Y=29072
X5293 VSS VDD 129 328 258 601 ICV_54 $T=14472 38880 0 0 $X=14472 $Y=38670
X5294 VSS VDD 608 58 199 636 ICV_54 $T=23112 21600 0 0 $X=23112 $Y=21390
X5295 VSS VDD 322 319 318 683 ICV_54 $T=35640 58320 1 0 $X=35640 $Y=57152
X5296 VSS VDD 293 303 358 334 ICV_54 $T=36288 23760 0 0 $X=36288 $Y=23550
X5297 VSS VDD 373 573 394 574 ICV_54 $T=43416 45360 1 0 $X=43416 $Y=44192
X5298 VSS VDD 569 578 390 391 ICV_54 $T=44280 54000 0 0 $X=44280 $Y=53790
X5299 VSS VDD 386 421 436 578 ICV_54 $T=48168 54000 0 0 $X=48168 $Y=53790
X5300 VSS VDD 634 94 90 495 22 AND4x1_ASAP7_75t_R $T=9072 32400 1 0 $X=9072 $Y=31232
X5301 VSS VDD 125 493 501 22 121 AND4x1_ASAP7_75t_R $T=12528 28080 1 0 $X=12528 $Y=26912
X5302 VSS VDD 7 6 166 139 146 AND4x1_ASAP7_75t_R $T=15984 38880 0 0 $X=15984 $Y=38730
X5303 VSS VDD 125 493 22 17 135 AND4x1_ASAP7_75t_R $T=16632 30240 1 0 $X=16632 $Y=29072
X5304 VSS VDD 7 6 5 135 604 AND4x1_ASAP7_75t_R $T=17496 43200 1 0 $X=17496 $Y=42032
X5305 VSS VDD 24 632 15 9 496 AND4x1_ASAP7_75t_R $T=20520 30240 0 0 $X=20520 $Y=30090
X5306 VSS VDD 608 248 513 58 205 AND4x1_ASAP7_75t_R $T=22464 21600 1 0 $X=22464 $Y=20432
X5307 VSS VDD 66 528 219 226 609 AND4x1_ASAP7_75t_R $T=24840 28080 0 0 $X=24840 $Y=27930
X5308 VSS VDD 66 236 677 244 249 AND4x1_ASAP7_75t_R $T=27648 41040 0 0 $X=27648 $Y=40890
X5309 VSS VDD 234 247 262 288 9 AND4x1_ASAP7_75t_R $T=29376 34560 1 0 $X=29376 $Y=33392
X5310 VSS VDD 44 363 614 346 374 AND4x1_ASAP7_75t_R $T=41256 25920 1 0 $X=41256 $Y=24752
X5311 VSS VDD 326 66 614 359 379 AND4x1_ASAP7_75t_R $T=41904 30240 1 0 $X=41904 $Y=29072
X5312 VSS VDD 569 380 376 357 394 AND4x1_ASAP7_75t_R $T=42768 51840 0 0 $X=42768 $Y=51690
X5313 VSS VDD 371 355 385 568 389 AND4x1_ASAP7_75t_R $T=42984 38880 1 0 $X=42984 $Y=37712
X5314 VSS VDD 382 371 355 393 416 AND4x1_ASAP7_75t_R $T=43416 36720 0 0 $X=43416 $Y=36570
X5315 VSS VDD 573 394 398 665 405 AND4x1_ASAP7_75t_R $T=44496 47520 1 0 $X=44496 $Y=46352
X5316 VSS VDD 582 416 574 398 614 AND4x1_ASAP7_75t_R $T=46224 43200 0 0 $X=46224 $Y=43050
X5317 VSS VDD 429 430 458 588 424 AND4x1_ASAP7_75t_R $T=47736 49680 0 0 $X=47736 $Y=49530
X5318 VSS VDD 416 424 578 587 402 AND4x1_ASAP7_75t_R $T=48168 43200 0 0 $X=48168 $Y=43050
X5319 VSS VDD 429 430 458 435 618 AND4x1_ASAP7_75t_R $T=48168 47520 1 0 $X=48168 $Y=46352
X5320 VSS VDD 10 674 110 496 80 OAI22x1_ASAP7_75t_R $T=10152 36720 0 0 $X=10152 $Y=36534
X5321 VSS VDD 337 312 124 258 333 OAI22x1_ASAP7_75t_R $T=35424 41040 0 0 $X=35424 $Y=40854
X5322 VSS VDD 324 642 353 350 643 OAI22x1_ASAP7_75t_R $T=38232 23760 1 0 $X=38232 $Y=22592
X5323 VSS VDD 327 330 590 478 446 OAI22x1_ASAP7_75t_R $T=48168 32400 1 0 $X=48168 $Y=31232
X5382 VSS VDD 129 91 104 274 HAxp5_ASAP7_75t_R $T=8424 43200 1 0 $X=8424 $Y=42032
X5383 VSS VDD 125 493 96 685 HAxp5_ASAP7_75t_R $T=9288 25920 1 0 $X=9288 $Y=24752
X5384 VSS VDD 125 493 155 688 HAxp5_ASAP7_75t_R $T=15984 25920 0 0 $X=15984 $Y=25832
X5385 VSS VDD 607 167 194 181 HAxp5_ASAP7_75t_R $T=21600 17280 0 0 $X=21600 $Y=17192
X5386 VSS VDD 202 167 209 689 HAxp5_ASAP7_75t_R $T=23544 15120 0 0 $X=23544 $Y=15032
X5387 VSS VDD 293 303 307 690 HAxp5_ASAP7_75t_R $T=34344 23760 0 0 $X=34344 $Y=23672
X5388 VSS VDD 293 303 308 283 HAxp5_ASAP7_75t_R $T=34344 25920 0 0 $X=34344 $Y=25832
X5389 VSS VDD 273 303 630 691 HAxp5_ASAP7_75t_R $T=35640 25920 1 0 $X=35640 $Y=24752
X5390 VSS VDD 420 414 383 617 HAxp5_ASAP7_75t_R $T=44280 34560 0 0 $X=44280 $Y=34472
X5391 VSS VDD 420 414 415 692 HAxp5_ASAP7_75t_R $T=46008 36720 1 0 $X=46008 $Y=35552
X5490 VSS VDD 23 26 29 515 NAND3x1_ASAP7_75t_R $T=19224 34560 1 0 $X=19224 $Y=33392
X5491 VSS VDD 388 330 573 579 NAND3x1_ASAP7_75t_R $T=44064 58320 1 0 $X=44064 $Y=57152
X5492 VSS VDD 394 573 373 419 NAND3x1_ASAP7_75t_R $T=44928 45360 1 0 $X=44928 $Y=44192
X5503 VSS VDD 234 247 262 24 135 257 AND5x1_ASAP7_75t_R $T=27648 34560 1 0 $X=27648 $Y=33392
X5504 VSS VDD 569 380 376 398 665 406 AND5x1_ASAP7_75t_R $T=44280 51840 0 0 $X=44280 $Y=51630
X5505 VSS VDD 191 8 BUFx4_ASAP7_75t_R $T=17712 38880 0 0 $X=17712 $Y=38792
X5506 VSS VDD 191 92 BUFx4_ASAP7_75t_R $T=21600 23760 0 0 $X=21600 $Y=23672
X5507 VSS VDD 191 210 BUFx4_ASAP7_75t_R $T=22896 17280 1 0 $X=22896 $Y=16112
X5508 VSS VDD 191 32 BUFx4_ASAP7_75t_R $T=29592 38880 1 0 $X=29592 $Y=37712
X5509 VSS VDD clk 191 BUFx4_ASAP7_75t_R $T=30672 32400 1 0 $X=30672 $Y=31232
X5510 VSS VDD 191 372 BUFx4_ASAP7_75t_R $T=41256 17280 1 0 $X=41256 $Y=16112
X5511 VSS VDD 191 46 BUFx4_ASAP7_75t_R $T=42768 47520 1 0 $X=42768 $Y=46352
X5512 VSS VDD 191 381 BUFx4_ASAP7_75t_R $T=43416 23760 1 0 $X=43416 $Y=22592
X5513 VSS VDD 191 48 BUFx4_ASAP7_75t_R $T=44280 41040 1 0 $X=44280 $Y=39872
X5514 VSS VDD 92 INVx3_ASAP7_75t_R $T=21600 25920 1 0 $X=21600 $Y=24752
X5515 VSS VDD 210 INVx3_ASAP7_75t_R $T=23976 19440 1 0 $X=23976 $Y=18272
X5516 VSS VDD 381 INVx3_ASAP7_75t_R $T=43416 23760 0 0 $X=43416 $Y=23672
X5517 VSS VDD 86 79 89 XOR2x2_ASAP7_75t_R $T=6264 45360 1 0 $X=6264 $Y=44192
X5518 VSS VDD 96 634 100 XOR2x2_ASAP7_75t_R $T=7560 23760 1 0 $X=7560 $Y=22592
X5519 VSS VDD 105 81 674 XOR2x2_ASAP7_75t_R $T=8856 45360 1 0 $X=8856 $Y=44192
X5520 VSS VDD 108 495 110 XOR2x2_ASAP7_75t_R $T=9288 34560 0 0 $X=9288 $Y=34306
X5521 VSS VDD 600 73 112 XOR2x2_ASAP7_75t_R $T=10152 41040 0 0 $X=10152 $Y=40786
X5522 VSS VDD 113 73 124 XOR2x2_ASAP7_75t_R $T=12096 43200 1 0 $X=12096 $Y=42032
X5523 VSS VDD 200 197 605 XOR2x2_ASAP7_75t_R $T=20952 45360 1 0 $X=20952 $Y=44192
X5524 VSS VDD 212 217 221 XOR2x2_ASAP7_75t_R $T=23760 54000 1 0 $X=23760 $Y=52832
X5525 VSS VDD 383 382 387 XOR2x2_ASAP7_75t_R $T=41904 41040 1 0 $X=41904 $Y=39872
X5526 VSS VDD 391 380 339 XOR2x2_ASAP7_75t_R $T=42768 56160 0 0 $X=42768 $Y=55906
X5527 VSS VDD 99 493 107 166 498 AOI211x1_ASAP7_75t_R $T=9288 23760 0 0 $X=9288 $Y=23672
X5528 VSS VDD 99 125 107 166 499 AOI211x1_ASAP7_75t_R $T=9720 19440 0 0 $X=9720 $Y=19352
X5529 VSS VDD 360 414 330 337 397 AOI211x1_ASAP7_75t_R $T=42768 34560 1 0 $X=42768 $Y=33392
X5544 VSS VDD 104 73 81 86 OR3x1_ASAP7_75t_R $T=7128 45360 0 0 $X=7128 $Y=45166
X5545 VSS VDD 82 81 88 113 OR3x1_ASAP7_75t_R $T=9504 43200 0 0 $X=9504 $Y=43006
X5546 VSS VDD 21 166 156 120 OR3x1_ASAP7_75t_R $T=14472 15120 0 0 $X=14472 $Y=14926
X5547 VSS VDD 75 259 130 30 OR3x1_ASAP7_75t_R $T=14904 47520 0 0 $X=14904 $Y=47326
X5548 VSS VDD 138 10 142 669 OR3x1_ASAP7_75t_R $T=16200 47520 0 0 $X=16200 $Y=47326
X5549 VSS VDD 5 153 134 158 OR3x1_ASAP7_75t_R $T=17496 41040 1 0 $X=17496 $Y=39872
X5550 VSS VDD 21 184 152 510 OR3x1_ASAP7_75t_R $T=18144 21600 1 0 $X=18144 $Y=20432
X5551 VSS VDD 29 627 134 511 OR3x1_ASAP7_75t_R $T=18144 36720 1 0 $X=18144 $Y=35552
X5552 VSS VDD 174 107 169 185 OR3x1_ASAP7_75t_R $T=19440 49680 1 0 $X=19440 $Y=48512
X5553 VSS VDD 163 194 518 143 OR3x1_ASAP7_75t_R $T=21384 19440 1 0 $X=21384 $Y=18272
X5554 VSS VDD 107 169 606 200 OR3x1_ASAP7_75t_R $T=22032 45360 0 0 $X=22032 $Y=45166
X5555 VSS VDD 197 606 30 214 OR3x1_ASAP7_75t_R $T=22896 56160 1 0 $X=22896 $Y=54992
X5556 VSS VDD 174 192 530 606 OR3x1_ASAP7_75t_R $T=23112 54000 0 0 $X=23112 $Y=53806
X5557 VSS VDD 178 206 215 219 OR3x1_ASAP7_75t_R $T=23328 28080 0 0 $X=23328 $Y=27886
X5558 VSS VDD 607 166 162 524 OR3x1_ASAP7_75t_R $T=23544 12960 1 0 $X=23544 $Y=11792
X5559 VSS VDD 259 118 606 216 OR3x1_ASAP7_75t_R $T=23976 51840 1 0 $X=23976 $Y=50672
X5560 VSS VDD 166 162 529 533 OR3x1_ASAP7_75t_R $T=25488 15120 0 0 $X=25488 $Y=14926
X5561 VSS VDD 21 107 509 188 OR3x1_ASAP7_75t_R $T=25488 19440 1 0 $X=25488 $Y=18272
X5562 VSS VDD 259 174 118 535 OR3x1_ASAP7_75t_R $T=25488 51840 1 0 $X=25488 $Y=50672
X5563 VSS VDD 259 118 225 235 OR3x1_ASAP7_75t_R $T=25920 49680 0 0 $X=25920 $Y=49486
X5564 VSS VDD 107 169 225 540 OR3x1_ASAP7_75t_R $T=26784 47520 0 0 $X=26784 $Y=47326
X5565 VSS VDD 174 192 30 242 OR3x1_ASAP7_75t_R $T=27000 54000 0 0 $X=27000 $Y=53806
X5566 VSS VDD 239 496 257 677 OR3x1_ASAP7_75t_R $T=27648 41040 1 0 $X=27648 $Y=39872
X5567 VSS VDD 278 280 225 31 OR3x1_ASAP7_75t_R $T=27648 49680 1 0 $X=27648 $Y=48512
X5568 VSS VDD 278 225 30 250 OR3x1_ASAP7_75t_R $T=27648 54000 1 0 $X=27648 $Y=52832
X5569 VSS VDD 107 169 31 663 OR3x1_ASAP7_75t_R $T=27864 45360 0 0 $X=27864 $Y=45166
X5570 VSS VDD 268 496 270 547 OR3x1_ASAP7_75t_R $T=29376 32400 0 0 $X=29376 $Y=32206
X5571 VSS VDD 273 340 324 664 OR3x1_ASAP7_75t_R $T=33264 25920 1 0 $X=33264 $Y=24752
X5572 VSS VDD 337 348 124 323 OR3x1_ASAP7_75t_R $T=35424 43200 1 0 $X=35424 $Y=42032
X5573 VSS VDD 308 338 321 325 OR3x1_ASAP7_75t_R $T=35856 30240 1 0 $X=35856 $Y=29072
X5574 VSS VDD 326 258 43 684 OR3x1_ASAP7_75t_R $T=40608 30240 1 0 $X=40608 $Y=29072
X5575 VSS VDD 86 107 494 OR2x2_ASAP7_75t_R $T=8640 45360 0 0 $X=8640 $Y=45272
X5576 VSS VDD 107 104 105 OR2x2_ASAP7_75t_R $T=9936 45360 0 0 $X=9936 $Y=45272
X5577 VSS VDD 113 107 600 OR2x2_ASAP7_75t_R $T=10368 43200 1 0 $X=10368 $Y=42032
X5578 VSS VDD 30 107 512 OR2x2_ASAP7_75t_R $T=18576 51840 1 0 $X=18576 $Y=50672
X5579 VSS VDD 118 259 169 OR2x2_ASAP7_75t_R $T=25488 49680 1 0 $X=25488 $Y=48512
X5580 VSS VDD 31 169 260 OR2x2_ASAP7_75t_R $T=28944 49680 1 0 $X=28944 $Y=48512
X5581 VSS VDD 292 21 640 OR2x2_ASAP7_75t_R $T=33912 17280 0 0 $X=33912 $Y=17192
X5582 VSS VDD 332 307 258 OR2x2_ASAP7_75t_R $T=38664 30240 0 0 $X=38664 $Y=30152
X5583 VSS VDD 350 326 331 OR2x2_ASAP7_75t_R $T=39744 25920 0 0 $X=39744 $Y=25832
X5584 VSS VDD 94 166 491 85 AOI21x1_ASAP7_75t_R $T=7560 30240 0 0 $X=7560 $Y=30086
X5585 VSS VDD 132 501 127 117 AOI21x1_ASAP7_75t_R $T=13176 30240 1 0 $X=13176 $Y=29072
X5586 VSS VDD 21 159 652 126 AOI21x1_ASAP7_75t_R $T=14472 36720 1 0 $X=14472 $Y=35552
X5587 VSS VDD 6 7 135 603 AOI21x1_ASAP7_75t_R $T=15552 43200 0 0 $X=15552 $Y=43046
X5588 VSS VDD 145 154 141 504 AOI21x1_ASAP7_75t_R $T=15768 15120 0 0 $X=15768 $Y=14966
X5589 VSS VDD 248 513 496 208 AOI21x1_ASAP7_75t_R $T=23976 21600 1 0 $X=23976 $Y=20432
X5590 VSS VDD 21 664 271 267 AOI21x1_ASAP7_75t_R $T=30672 23760 0 0 $X=30672 $Y=23606
X5591 VSS VDD 21 268 270 554 AOI21x1_ASAP7_75t_R $T=30672 32400 0 0 $X=30672 $Y=32246
X5592 VSS VDD 21 295 282 553 AOI21x1_ASAP7_75t_R $T=31752 28080 0 0 $X=31752 $Y=27926
X5593 VSS VDD 348 350 334 642 AOI21x1_ASAP7_75t_R $T=38016 23760 0 0 $X=38016 $Y=23606
X5594 VSS VDD 331 358 565 337 AOI21x1_ASAP7_75t_R $T=39528 25920 1 0 $X=39528 $Y=24752
X5595 VSS VDD 139 166 23 503 198 176 AO32x1_ASAP7_75t_R $T=19872 38880 1 0 $X=19872 $Y=37712
X5596 VSS VDD 253 166 66 245 662 539 AO32x1_ASAP7_75t_R $T=27864 38880 1 0 $X=27864 $Y=37712
X5597 VSS VDD 246 336 338 254 362 291 AO32x1_ASAP7_75t_R $T=33696 51840 1 0 $X=33696 $Y=50672
X5598 VSS VDD 686 336 338 639 362 653 AO32x1_ASAP7_75t_R $T=34344 51840 0 0 $X=34344 $Y=51706
X5599 VSS VDD 254 336 338 264 362 301 AO32x1_ASAP7_75t_R $T=34560 49680 1 0 $X=34560 $Y=48512
X5600 VSS VDD 264 336 338 329 362 641 AO32x1_ASAP7_75t_R $T=35424 47520 0 0 $X=35424 $Y=47386
X5601 VSS VDD 399 43 66 349 653 351 AO32x1_ASAP7_75t_R $T=38880 51840 0 0 $X=38880 $Y=51706
X5602 VSS VDD 687 530 198 XNOR2x1_ASAP7_75t_R $T=20520 51840 1 0 $X=20520 $Y=50672
X5603 VSS VDD 214 217 531 XNOR2x1_ASAP7_75t_R $T=24408 56160 0 0 $X=24408 $Y=55978
X5604 VSS VDD 242 530 261 XNOR2x1_ASAP7_75t_R $T=28296 54000 0 0 $X=28296 $Y=53818
X5605 VSS VDD 250 280 686 XNOR2x1_ASAP7_75t_R $T=28944 54000 1 0 $X=28944 $Y=52832
X5606 VSS VDD 260 34 277 XNOR2x1_ASAP7_75t_R $T=30240 49680 1 0 $X=30240 $Y=48512
X5607 VSS VDD 79 82 73 81 88 130 OR5x1_ASAP7_75t_R $T=7776 43200 0 0 $X=7776 $Y=42990
X5608 VSS VDD 104 79 75 73 81 118 OR5x1_ASAP7_75t_R $T=11880 45360 0 0 $X=11880 $Y=45150
X5609 VSS VDD 515 187 173 206 99 509 OR5x1_ASAP7_75t_R $T=20520 28080 0 0 $X=20520 $Y=27870
X5610 VSS VDD 174 192 217 530 197 225 OR5x1_ASAP7_75t_R $T=25272 54000 0 0 $X=25272 $Y=53790
X5611 VSS VDD 34 232 169 31 258 263 OR5x1_ASAP7_75t_R $T=28728 45360 1 0 $X=28728 $Y=44192
X5612 VSS VDD ICV_66 $T=14256 56160 0 0 $X=14256 $Y=56072
X5613 VSS VDD ICV_66 $T=14256 58320 0 0 $X=14256 $Y=58232
X5667 VSS VDD ICV_67 $T=4320 4320 0 0 $X=4320 $Y=4232
X5668 VSS VDD ICV_67 $T=4320 6480 0 0 $X=4320 $Y=6392
X5669 VSS VDD ICV_67 $T=4320 8640 0 0 $X=4320 $Y=8552
X5670 VSS VDD ICV_67 $T=4320 10800 0 0 $X=4320 $Y=10712
X5671 VSS VDD ICV_67 $T=4320 49680 0 0 $X=4320 $Y=49592
X5672 VSS VDD ICV_67 $T=4320 51840 0 0 $X=4320 $Y=51752
X5673 VSS VDD ICV_67 $T=4320 54000 0 0 $X=4320 $Y=53912
X5674 VSS VDD ICV_67 $T=4320 56160 0 0 $X=4320 $Y=56072
X5675 VSS VDD ICV_67 $T=4320 58320 0 0 $X=4320 $Y=58232
.ENDS
***************************************
