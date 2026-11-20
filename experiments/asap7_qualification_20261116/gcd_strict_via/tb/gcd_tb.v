module gcd_tb;
reg clk=0; always #5 clk=~clk;
reg reset=1,req_val=0,resp_rdy=1;reg[31:0]req_msg=0;
wire req_rdy,resp_val;wire[15:0]resp_msg;
gcd dut(clk,req_msg,req_rdy,req_val,reset,resp_msg,resp_rdy,resp_val);
task check;input[15:0]a,b,expected;integer n;
begin
 @(negedge clk);while(!req_rdy)@(negedge clk);req_msg={a,b};req_val=1;
 @(negedge clk);req_val=0;n=0;
 while(!resp_val&&n<2000)begin @(negedge clk);n=n+1;end
 if(!resp_val||resp_msg!==expected)$fatal(1,"gcd %d %d got %d expected %d",a,b,resp_msg,expected);
 @(negedge clk);
end endtask
initial begin repeat(3)@(negedge clk);reset=0;check(48,18,6);check(270,192,6);check(17,13,1);check(100,25,25);check(65535,255,255);$display("PASS: 5 GCD vectors");$finish;end
initial begin #200000;$fatal(1,"timeout");end
endmodule
