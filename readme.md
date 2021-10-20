# Test Repo for RPC DRAM on OrangeCrab

## Usage
Setup, pull down open pull request
```
$ cd litedram/
deps/litedram$ git fetch origin pull/273/head:antmicro-rpc
remote: Enumerating objects: 21, done.
remote: Counting objects: 100% (21/21), done.
remote: Compressing objects: 100% (9/9), done.
remote: Total 21 (delta 12), reused 21 (delta 12), pack-reused 0
Unpacking objects: 100% (21/21), 24.01 KiB | 132.00 KiB/s, done.
From https://github.com/enjoy-digital/litedram
 * [new ref]         refs/pull/273/head -> antmicro-rpc
deps/litedram$ git checkout antmicro-rpc 
Switched to branch 'antmicro-rpc'
```

Build
```console
$ ./rpc-bitstream.py --ecppack-compress --ecppack-spimode qspi --build --load
```

Test
```console
$ wishbone-tool --pid 0x5af0 0x00000004 
Value at 00000004: 12345678
```