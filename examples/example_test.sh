#!/bin/sh
# Run from build dir!!!
examples_dir=$(dirname $0)
rootdir=$(dirname $examples_dir)
builddir="$rootdir/build"
#testc="$examples_dir/test.c"
testc="$examples_dir/justmain.c"
#testc="$examples_dir/simple.c"
testbc="$examples_dir/test.bc"
outbc="$examples_dir/out.bc"
outbc_split="$examples_dir/out_split.bc"
outbc_mark="$examples_dir/out_mark.bc"
outbc_markll="$examples_dir/out_mark.ll"
outfile="$examples_dir/test_tracing"

plugin="$builddir/src/pass/libGigaFuncPass.dylib"
runtime="$builddir/src/gfrt/libgigafuncruntime.a"
traceio="$builddir/src/traceio/libtraceio.a"

mkdir $builddir
$(cd $builddir; cmake .. ; make)

echo "Building $testbc"
/usr/local/opt/llvm/bin/clang -O0 -Xclang -disable-O0-optnone -emit-llvm -g -o $testbc -c $testc
echo "Split basic blocks $testbc -> $outbc_split"
/usr/local/opt/llvm/bin/opt -load-pass-plugin  $plugin --passes="splitbasicblocks" -o $outbc_split $testbc
echo "Mark basic blocks $outbc_split -> $outbc_mark"
/usr/local/opt/llvm/bin/opt -load-pass-plugin  $plugin --passes="markbasicblocks" -o $outbc_mark $outbc_split
/usr/local/opt/llvm/bin/opt -load-pass-plugin  $plugin --passes="markbasicblocks" -S -o $outbc_markll $outbc_split
echo "Instrument basic blocks $outbc_mark -> $outbc"
/usr/local/opt/llvm/bin/opt -load-pass-plugin  $plugin --passes="instrumentbasicblocks" -o $outbc $outbc_mark
echo "Final binary: $outfile"
/usr/local/opt/llvm/bin/clang++ -g $outbc $runtime $traceio -o $outfile
