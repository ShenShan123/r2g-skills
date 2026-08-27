module future_prospective_logic_v23 (
    input         clk,
    input         valid,
    input  [3:0]  opcode,
    input  [15:0] value,
    output reg [15:0] result,
    output reg        done
);
  always @(posedge clk) begin
    done <= valid;
    if (valid) begin
      case (opcode[1:0])
        2'b00: result <= value + 16'h0001;
        2'b01: result <= value - 16'h0001;
        2'b10: result <= value << 1;
        default: result <= value ^ 16'h00ff;
      endcase
    end
  end
endmodule
