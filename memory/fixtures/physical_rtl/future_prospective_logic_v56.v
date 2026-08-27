module future_prospective_logic_v56 (
    input wire clk, input wire valid, input wire [3:0] opcode, input wire [15:0] value,
    output reg [15:0] result, output reg done
);
  always @(posedge clk) begin
    done <= valid;
    if (valid) result <= opcode[1] ? (value << 1) : (value + 16'h001d);
  end
endmodule
