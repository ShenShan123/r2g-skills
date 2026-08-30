module future_prospective_logic_v116 (
    input wire clk, input wire rst_n, input wire step,
    input wire [7:0] seed, output reg [7:0] state, output reg active
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin state <= 8'h3c; active <= 1'b0; end
    else begin
      active <= step;
      if (step) state <= {state[6:0], state[7] ^ state[3]} ^ seed;
    end
  end
endmodule
