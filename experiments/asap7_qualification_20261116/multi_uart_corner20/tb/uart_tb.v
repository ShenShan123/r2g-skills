`timescale 1ns/1ps
module uart_tb;
  localparam integer PRESCALE = 4;
  localparam integer BIT_CYCLES = PRESCALE * 8;
  reg clk = 0;
  always #0.5 clk = ~clk;
  reg rst = 1;
  reg [7:0] s_axis_tdata = 0;
  reg s_axis_tvalid = 0;
  wire s_axis_tready;
  wire [7:0] m_axis_tdata;
  wire m_axis_tvalid;
  reg m_axis_tready = 0;
  reg rxd = 1;
  wire txd, tx_busy, rx_busy, rx_overrun_error, rx_frame_error;
  integer framing_events = 0, overrun_events = 0;
  integer tx_frames = 0, rx_frames = 0;
  uart dut(.clk(clk), .rst(rst), .s_axis_tdata(s_axis_tdata),
    .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
    .m_axis_tdata(m_axis_tdata), .m_axis_tvalid(m_axis_tvalid),
    .m_axis_tready(m_axis_tready), .rxd(rxd), .txd(txd),
    .tx_busy(tx_busy), .rx_busy(rx_busy), .rx_overrun_error(rx_overrun_error),
    .rx_frame_error(rx_frame_error), .prescale(16'd4));
  always @(posedge clk) begin
    if (!rst && rx_frame_error) framing_events = framing_events + 1;
    if (!rst && rx_overrun_error) overrun_events = overrun_events + 1;
  end

  task reset_uart;
    begin
      @(negedge clk); rst = 1; s_axis_tvalid = 0; m_axis_tready = 0; rxd = 1;
      repeat (4) @(negedge clk);
      rst = 0;
      repeat (4) @(negedge clk);
      if (txd !== 1 || tx_busy !== 0 || m_axis_tvalid !== 0)
        $fatal(1, "reset/idle outputs incorrect");
    end
  endtask

  task check_tx;
    input [7:0] value;
    integer k;
    begin
      while (!s_axis_tready) @(negedge clk);
      @(negedge clk); s_axis_tdata = value; s_axis_tvalid = 1;
      @(posedge clk); #0.05;
      if (txd !== 0 || tx_busy !== 1) $fatal(1, "TX start/handshake failure");
      @(negedge clk); s_axis_tvalid = 0;
      repeat (BIT_CYCLES/2-1) @(posedge clk);
      #0.05;
      if (txd !== 0) $fatal(1, "TX start bit missing");
      for (k=0; k<8; k=k+1) begin
        repeat (BIT_CYCLES) @(posedge clk);
        #0.05;
        if (txd !== value[k]) $fatal(1, "TX data mismatch value=%h bit=%0d", value, k);
      end
      repeat (BIT_CYCLES) @(posedge clk);
      #0.05;
      if (txd !== 1) $fatal(1, "TX stop bit missing");
      while (tx_busy) @(negedge clk);
      tx_frames = tx_frames + 1;
      $display("TX PASS %h", value);
    end
  endtask

  task drive_rx;
    input [7:0] value;
    input good_stop;
    integer k;
    begin
      @(negedge clk); rxd = 0;
      repeat (BIT_CYCLES) @(negedge clk);
      for (k=0; k<8; k=k+1) begin
        rxd = value[k];
        repeat (BIT_CYCLES) @(negedge clk);
      end
      rxd = good_stop;
      repeat (BIT_CYCLES) @(negedge clk);
      rxd = 1;
      repeat (BIT_CYCLES) @(negedge clk);
    end
  endtask

  task check_rx;
    input [7:0] value;
    begin
      drive_rx(value, 1);
      if (m_axis_tvalid !== 1 || m_axis_tdata !== value)
        $fatal(1, "RX data mismatch expected=%h actual=%h", value, m_axis_tdata);
      repeat (12) begin
        @(negedge clk);
        if (m_axis_tvalid !== 1 || m_axis_tdata !== value)
          $fatal(1, "RX output did not hold during backpressure");
      end
      m_axis_tready = 1;
      repeat (2) @(negedge clk);
      m_axis_tready = 0;
      if (m_axis_tvalid !== 0) $fatal(1, "RX handshake did not clear valid");
      rx_frames = rx_frames + 1;
      $display("RX PASS %h with backpressure", value);
    end
  endtask

  initial begin
    reset_uart;
    check_tx(8'h00); check_tx(8'hff); check_tx(8'ha5); check_tx(8'h5a); check_tx(8'h31);
    check_rx(8'h00); check_rx(8'hff); check_rx(8'ha5);
    if (framing_events || overrun_events) $fatal(1, "unexpected error on valid frames");
    drive_rx(8'h6c, 0);
    if (framing_events != 1 || m_axis_tvalid !== 0)
      $fatal(1, "bad stop bit must raise frame error and suppress valid");
    reset_uart;
    drive_rx(8'h12, 1);
    if (!m_axis_tvalid || m_axis_tdata !== 8'h12) $fatal(1, "first overrun-test frame missing");
    drive_rx(8'h34, 1);
    if (overrun_events != 1 || !m_axis_tvalid || m_axis_tdata !== 8'h34)
      $fatal(1, "overrun indication/latest-byte behavior incorrect");
    rx_frames = rx_frames + 2;
    $display("PASS tx_frames=%0d rx_frames=%0d backpressure_cases=3 frame_errors=%0d overrun_errors=%0d",
      tx_frames, rx_frames, framing_events, overrun_events);
    $finish;
  end
  initial begin
    repeat (20000) @(posedge clk);
    $fatal(1, "test timeout");
  end
endmodule
