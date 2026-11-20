from pathlib import Path
out=Path(__file__).resolve().parent
code=[]; labels={}; pending=[]; asm=[]
def emit(w,s): code.append(w&0xffffffff); asm.append(s)
def label(s): labels[s]=4*len(code)
def I(op,rd,rs,imm,f=0): return ((imm&4095)<<20)|(rs<<15)|(f<<12)|(rd<<7)|op
def R(rd,a,b,f=0,hi=0): return (hi<<25)|(b<<20)|(a<<15)|(f<<12)|(rd<<7)|0x33
def S(rs,base,imm,f=2): return ((imm&0xfe0)<<20)|(rs<<20)|(base<<15)|(f<<12)|((imm&31)<<7)|0x23
def br(a,b,f,target): pending.append((len(code),'b',target,a,b,f)); emit(0,f'branch f{f} x{a},x{b},{target}')
def jump(rd,target): pending.append((len(code),'j',target,rd,0,0)); emit(0,f'jal x{rd},{target}')
def addi(rd,rs,n): emit(I(0x13,rd,rs,n),f'addi x{rd},x{rs},{n}')
def sw(rs,off): emit(S(rs,20,off),f'sw x{rs},{off}(x20)')
addi(20,0,256); addi(1,0,10); addi(2,0,0)
label('sum'); emit(R(2,2,1),'add x2,x2,x1'); addi(1,1,-1); br(1,0,1,'sum'); sw(2,0)
emit(I(3,3,20,0,2),'lw x3,0(x20)'); br(3,2,1,'fail')
addi(4,0,0); addi(5,0,1); addi(6,0,10)
label('fib'); emit(R(7,4,5),'add x7,x4,x5'); addi(4,5,0); addi(5,7,0); addi(6,6,-1); br(6,0,1,'fib'); sw(4,4)
addi(8,0,-1); emit(I(0x13,9,8,4,1),'slli x9,x8,4'); emit(I(0x13,10,9,4,5),'srli x10,x9,4'); sw(10,8)
emit(R(11,8,0,2),'slt x11,x8,x0'); sw(11,12)
emit(S(8,20,16,0),'sb x8,16(x20)'); emit(I(3,12,20,16,0),'lb x12,16(x20)'); sw(12,20)
emit(I(3,13,20,16,4),'lbu x13,16(x20)'); sw(13,24)
jump(1,'subroutine'); sw(14,28)
addi(15,0,0x600); addi(15,15,13); sw(15,32); jump(0,'done')
label('subroutine'); addi(14,0,42); emit(I(0x67,0,1,0),'jalr x0,0(x1)')
label('fail'); addi(15,0,-1); sw(15,32)
label('done'); emit(0x00100073,'ebreak')
for i,kind,target,a,b,f in pending:
 d=labels[target]-4*i
 if kind=='b': w=(((d>>12)&1)<<31)|(((d>>5)&63)<<25)|(b<<20)|(a<<15)|(f<<12)|(((d>>1)&15)<<8)|(((d>>11)&1)<<7)|0x63
 else: w=(((d>>20)&1)<<31)|(((d>>1)&1023)<<21)|(((d>>11)&1)<<20)|(((d>>12)&255)<<12)|(a<<7)|0x6f
 code[i]=w
(out/'program.hex').write_text(''.join(f'{x:08x}\n' for x in code))
(out/'program.S').write_text('\n'.join(f'{i*4:04x}: {w:08x}  {s}' for i,(w,s) in enumerate(zip(code,asm)))+'\n')
print(len(code))
