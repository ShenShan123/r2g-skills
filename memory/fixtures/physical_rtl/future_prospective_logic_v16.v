module future_prospective_logic_v16 (
    input         clk,
    input         rst_n,
    input         step,
    input  [7:0]  data_in,
    output reg [7:0] state,
    output reg       done
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= 8'h00;
      done <= 1'b0;
    end else if (step) begin
      state <= state + data_in;
      done <= (state + data_in) == 8'hff;
    end
  end
endmodule
