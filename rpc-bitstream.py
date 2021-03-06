#!/usr/bin/env python3

# Copyright (c) Greg Davill <greg.davill@gmail.com>
# SPDX-License-Identifier: BSD-2-Clause

import os
import sys
import argparse

# Prepend our local deps
sys.path.insert(1, "deps/valentyusb")
sys.path.insert(1, "deps/litedram")

from migen import *
from migen.genlib.misc import WaitTimer
from migen.genlib.resetsync import AsyncResetSynchronizer


from litex.build.lattice.trellis import trellis_args, trellis_argdict

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser

from litedram.modules import EM6GA16L

from ecp5phy import ECP5RPCPHY, ECP5RPCPads
import gsd_orangecrab as orangecrab 

# CRG ---------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq, with_usb_pll=False):
        self.rst = Signal()
        self.clock_domains.cd_por = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys = ClockDomain()

        # # #

        # Clk / Rst
        clk48 = platform.request("clk48")
        rst_n = platform.request("usr_btn", loose=True)
        if rst_n is None: rst_n = 1

        # Power on reset
        por_count = Signal(16, reset=2**16-1)
        por_done  = Signal()
        self.comb += self.cd_por.clk.eq(clk48)
        self.comb += por_done.eq(por_count == 0)
        self.sync.por += If(~por_done, por_count.eq(por_count - 1))

        # PLL
        self.submodules.pll = pll = ECP5PLL()
        self.comb += pll.reset.eq(~por_done | ~rst_n | self.rst)
        pll.register_clkin(clk48, 48e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)

        # USB PLL
        if with_usb_pll:
            self.clock_domains.cd_usb_12 = ClockDomain()
            self.clock_domains.cd_usb_48 = ClockDomain()
            usb_pll = ECP5PLL()
            self.submodules += usb_pll
            self.comb += usb_pll.reset.eq(~por_done)
            usb_pll.register_clkin(clk48, 48e6)
            usb_pll.create_clkout(self.cd_usb_48, 48e6)
            usb_pll.create_clkout(self.cd_usb_12, 12e6)

        # FPGA Reset (press usr_btn for 1 second to fallback to bootloader)
        reset_timer = WaitTimer(int(48e6))
        reset_timer = ClockDomainsRenamer("por")(reset_timer)
        self.submodules += reset_timer
        self.comb += reset_timer.wait.eq(~rst_n)
        self.comb += platform.request("rst_n").eq(~reset_timer.done)


class _CRGSDRAM(Module):
    def __init__(self, platform, sys_clk_freq, with_usb_pll=False):
        self.rst = Signal()
        self.clock_domains.cd_init     = ClockDomain()
        self.clock_domains.cd_por      = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys      = ClockDomain()
        self.clock_domains.cd_sys2x    = ClockDomain()
        self.clock_domains.cd_sys2x_i  = ClockDomain(reset_less=True)

        # # #

        self.stop  = Signal()
        self.reset = Signal()

        # Clk / Rst
        clk48 = platform.request("clk48")
        rst_n = platform.request("usr_btn", loose=True)
        if rst_n is None: rst_n = 1

        # Power on reset
        por_count = Signal(16, reset=2**16-1)
        por_done  = Signal()
        self.comb += self.cd_por.clk.eq(clk48)
        self.comb += por_done.eq(por_count == 0)
        self.sync.por += If(~por_done, por_count.eq(por_count - 1))

        # PLL
        sys2x_clk_ecsout = Signal()
        self.submodules.pll = pll = ECP5PLL()
        self.comb += pll.reset.eq(~por_done | ~rst_n | self.rst)
        pll.register_clkin(clk48, 48e6)
        pll.create_clkout(self.cd_sys2x_i, 2*sys_clk_freq)
        pll.create_clkout(self.cd_init, 24e6)
        self.specials += [
            Instance("ECLKBRIDGECS",
                i_CLK0   = self.cd_sys2x_i.clk,
                i_SEL    = 0,
                o_ECSOUT = sys2x_clk_ecsout),
            Instance("ECLKSYNCB",
                i_ECLKI = sys2x_clk_ecsout,
                i_STOP  = self.stop,
                o_ECLKO = self.cd_sys2x.clk),
            Instance("CLKDIVF",
                p_DIV     = "2.0",
                i_ALIGNWD = 0,
                i_CLKI    = self.cd_sys2x.clk,
                i_RST     = self.reset,
                o_CDIVX   = self.cd_sys.clk),
            AsyncResetSynchronizer(self.cd_sys,   ~pll.locked | self.reset),
            AsyncResetSynchronizer(self.cd_sys2x, ~pll.locked | self.reset),
        ]

        # USB PLL
        if with_usb_pll:
            self.clock_domains.cd_usb_12 = ClockDomain()
            self.clock_domains.cd_usb_48 = ClockDomain()
            usb_pll = ECP5PLL()
            self.submodules += usb_pll
            self.comb += usb_pll.reset.eq(~por_done)
            usb_pll.register_clkin(clk48, 48e6)
            usb_pll.create_clkout(self.cd_usb_48, 48e6)
            usb_pll.create_clkout(self.cd_usb_12, 12e6)

        # FPGA Reset (press usr_btn for 1 second to fallback to bootloader)
        reset_timer = WaitTimer(int(48e6))
        reset_timer = ClockDomainsRenamer("por")(reset_timer)
        self.submodules += reset_timer
        self.comb += reset_timer.wait.eq(~rst_n)
        self.comb += platform.request("rst_n").eq(~reset_timer.done)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, revision="0.2", device="25F", sdram_device="MT41K64M16",
                 sys_clk_freq=int(48e6), toolchain="trellis", with_led_chaser=True, **kwargs):
        platform = orangecrab.Platform(revision=revision, device=device ,toolchain=toolchain)


        kwargs['uart_name'] = 'stream'
        if kwargs["uart_name"] != "usb_acm":
            kwargs['cpu_type'] = None
        # kwargs["uart_name"] = "usb_acm"

        # SoCCore ----------------------------------------------------------------------------------
        SoCCore.__init__(self, platform, sys_clk_freq,
            ident          = "LiteX RPC Test SoC (OrangeCrab)",
            ident_version  = True,
            **kwargs)

        # CRG --------------------------------------------------------------------------------------
        crg_cls = _CRGSDRAM if not self.integrated_main_ram_size else _CRG
        self.submodules.crg = crg_cls(platform, sys_clk_freq, with_usb_pll=True)

        # RPC-DRAM ---------------------------------------------------------------------------------
        ddram_pads = platform.request("rpc_dram")
        self.submodules.ddrphy = ECP5RPCPHY(
            pads         = ECP5RPCPads(ddram_pads),
            sys_clk_freq = sys_clk_freq,
            )
        self.ddrphy.settings.rtt_nom = "disabled"


        if hasattr(ddram_pads, "vccio"):
            self.comb += ddram_pads.vccio.eq(Replicate(1,len(ddram_pads.vccio)))
        if hasattr(ddram_pads, "gnd"):
            self.comb += ddram_pads.gnd.eq(0)
        self.comb += self.crg.stop.eq(self.ddrphy.init.stop)
        self.comb += self.crg.reset.eq(self.ddrphy.init.reset)
        self.add_sdram("sdram",
            phy           = self.ddrphy,
            module        = EM6GA16L(sys_clk_freq, "1:2"),
            l2_cache_size = kwargs.get("l2_size", 8192)
        )

        # Leds -------------------------------------------------------------------------------------
        if with_led_chaser:
            self.submodules.leds = LedChaser(
                pads         = platform.request_all("user_led"),
                sys_clk_freq = sys_clk_freq)

        
        # Wishbone Bridge through DummyUsb ---------------------------------------------------------
        if kwargs["uart_name"] != "usb_acm":
            import valentyusb.usbcore.io as usbio
            from valentyusb.usbcore.cpu.dummyusb import DummyUsb

            usb_pads = self.platform.request("usb")
            usb_iobuf = usbio.IoBuf(usb_pads.d_p, usb_pads.d_n, usb_pads.pullup)
            self.submodules.bridge = (DummyUsb(usb_iobuf, debug=True, cdc=True, product="wishbone-bridge", manufacturer="GsD", pid=0x5af0))
            self.bus.add_master(name="bridge", master=self.bridge.debug_bridge.wishbone)


# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on OrangeCrab")
    parser.add_argument("--build",           action="store_true",  help="Build bitstream")
    parser.add_argument("--load",            action="store_true",  help="Load bitstream")
    parser.add_argument("--toolchain",       default="trellis",    help="FPGA  use, trellis (default) or diamond")
    parser.add_argument("--sys-clk-freq",    default=48e6,         help="System clock frequency (default: 48MHz)")
    parser.add_argument("--revision",        default="0.2",        help="Board Revision: 0.1 or 0.2 (default)")
    parser.add_argument("--device",          default="85F",        help="ECP5 device (default: 25F)")
    parser.add_argument("--sdram-device",    default="MT41K64M16", help="SDRAM device (default: MT41K64M16)")
    parser.add_argument("--with-spi-sdcard", action="store_true",  help="Enable SPI-mode SDCard support")
    builder_args(parser)
    soc_core_args(parser)
    trellis_args(parser)
    args = parser.parse_args()

    soc = BaseSoC(
        toolchain    = args.toolchain,
        revision     = args.revision,
        device       = args.device,
        sdram_device = args.sdram_device,
        sys_clk_freq = int(float(args.sys_clk_freq)),
        **soc_core_argdict(args))
    if args.with_spi_sdcard:
        soc.add_spi_sdcard()
    builder = Builder(soc, **builder_argdict(args))
    builder_kargs = trellis_argdict(args) if args.toolchain == "trellis" else {}
    builder.build(**builder_kargs, run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
