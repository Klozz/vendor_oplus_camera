#!/usr/bin/env -S PYTHONPATH=../../../tools/extract-utils python3
#
# SPDX-FileCopyrightText: 2016 The CyanogenMod Project
# SPDX-FileCopyrightText: 2017-2024 The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from extract_utils.fixups_lib import (
    lib_fixups,
    lib_fixups_user_type,
)
from extract_utils.fixups_blob import (
    blob_fixup,
    blob_fixups_user_type,
)
from extract_utils.main import (
    ExtractUtils,
    ExtractUtilsModule,
)


def lib_fixup_system_ext_suffix(lib: str, partition: str, *args, **kwargs):
    """
    Mirrors lib_to_package_fixup_system_ext_variants from the old setup-makefiles.sh.
    These libs exist as system_ext variants and need a _system_ext suffix
    when pulled from that partition.
    """
    if partition != 'system_ext':
        return None

    system_ext_libs = {
        'libSuperTextWrapper',
        'libXDocProcessSDK',
        'libYTCommon',
        'libmpbase',
        'libextendfile',
    }

    return f'{lib}_system_ext' if lib in system_ext_libs else None


lib_fixups: lib_fixups_user_type = {
    # **lib_fixups already includes the clang RT ubsan and proto 3.9.1
    # fixups that were previously handled by the bash helper functions
    # lib_to_package_fixup_clang_rt_ubsan_standalone and
    # lib_to_package_fixup_proto_3_9_1 — no need to add them explicitly.
    **lib_fixups,
    (
        'libSuperTextWrapper',
        'libXDocProcessSDK',
        'libYTCommon',
        'libmpbase',
        'libextendfile',
    ): lib_fixup_system_ext_suffix,
}

blob_fixups = {
    'system_ext/priv-app/OplusCamera/OplusCamera.apk': blob_fixup()
        .apktool_patch('patches'),
    'system_ext/framework/com.oplus.camera.unit.sdk.jar': blob_fixup()
        .apktool_patch('patches-sdk'),
    'system_ext/priv-app/OppoGallery2/OppoGallery2.apk': blob_fixup()
        .apktool_patch('patches-gallery'),
    'odm/etc/init/init.camera_process.rc': blob_fixup()
        .regex_replace(
            '''on post-fs-data
    mkdir /data/vendor/camera_process 0777 camera camera
    mkdir /data/vendor/camera_process/livephoto 0777 camera camera
    mkdir /data/vendor/cam_alog 0777 camera camera
on property:sys.camera.user.removed=*
    #delete_recursion /data/vendor/camera_process/${sys.camera.user.removed}
''',
            '''on post-fs-data
    mkdir /data/vendor/camera_process 0777 camera camera
    mkdir /data/vendor/camera_process/livephoto 0777 camera camera
    mkdir /data/vendor/cam_alog 0777 camera camera
    # APS file storage for deferred-capture jobs (matches stock init.oplus.rootdir.rc).
    # Without these, APSFileStorage can't mkdir under system-owned /data/system,
    # defer-job params are never persisted (keepJob "Not found in FileSystem"),
    # and the offline metadata collapses to empty -> photo-capture crash.
    mkdir /data/system/camera_rus 0777 cameraserver cameraserver
    mkdir /data/vendor/camera_rus 0777 camera camera
on property:sys.camera.user.removed=*
    #delete_recursion /data/vendor/camera_process/${sys.camera.user.removed}
''',
        ),
    'odm/lib64/libAlgoProcess.so': blob_fixup()
        .replace_needed('android.hardware.graphics.common-V5-ndk.so', 'android.hardware.graphics.common-V7-ndk.so')
        # APS capture crash + soft photo (IPE p010 stage), root-caused via native Frida
        # (aps_p010_args_probe.js) + static RE of the converter:
        # android::hwIPEDoProcess does an IN-PLACE 10-bit LSB->MSB conversion via
        # APSFormatConverterNeon::p010LSB2MSBNeon(dst, src=dst, a2, a3, a4, a5). The converter
        # loop is CONTIGUOUS (`ldr q0,[x11],#0x10`; no per-row stride), so a4/a5 are NOT strides
        # -- they only set the LENGTH converted: bytes = a4*a5*1.5 (mul w22,w21; *3; >>5 -> NEON
        # iters of 16B). a2/a3 are non-zero guards only, IGNORED. The buffer is a 4096x3072 NV12
        # p010 image: Y plane = w*h*2 = 0x1800000 (25.16 MB) FIRST, then UV plane (12.58 MB),
        # total 0x2400000 (36 MiB, = the live rw mapping). Only the Y plane is LSB-packed and
        # needs the <<6; the UV plane is already MSB-aligned and must NOT be shifted (shifting it
        # overflows the chroma -> sharp-but-GREEN photo). The caller loads a4=stride[0x2f4c]=8192
        # and a5 from [x28,#0x2f50] which is UNINITIALIZED on this port (0xfc8.. garbage) -> a5
        # huge -> SIGSEGV (or APS bails -> soft QuickJpeg). Fix: make the converted length equal
        # the Y plane exactly: a4*a5*1.5 == 0x1800000 => a4*a5 == 0x1000000 == 4096*4096 == w*w.
        # Load WIDTH [0x2f2c] (=4096) into BOTH a4 and a5:
        #   ldr w4,[x28,#0x2f4c] (b96f4f84) -> ldr w4,[x28,#0x2f2c] (b96f2f84)  (4f->2f)
        #   ldr w5,[x28,#0x2f50] (b96f5385) -> ldr w5,[x28,#0x2f2c] (b96f2f85)  (53->2f, +5350->2f)
        # History: 53->4f ("srcStride==dstStride") crashed (8192*8192 overrun); 53->33 (a5=height)
        # converted the WHOLE buffer incl UV -> green; w*w converts the Y plane only -> correct.
        # Same struct-field/contract-mismatch family as the offlinecamera +0x1c->+0x20 fix.
        # 12-byte anchor = ldr w3,[x28,#0x2f30]; ldr w4,[x28,#0x2f4c]; ldr w5,[x28,#0x2f50].
        .binary_regex_replace(
            b'\x83\x33\x6f\xb9\x84\x4f\x6f\xb9\x85\x53\x6f\xb9',
            b'\x83\x33\x6f\xb9\x84\x2f\x6f\xb9\x85\x2f\x6f\xb9',
        )
        # SKIP the turbo-RAW-HDR stage in APSCaptureModeManager::workRoutine. Traced (HW
        # bisect + aps_blr2/alloc probes): the turbo-HDR OUTPUT buffer VA (AlgoProcessData+0x540)
        # is a 4GB-aligned, CPU-unmapped (PROT_NONE) reservation -- align_up_4GB(src). ArcSoft
        # (ARC_Turbo_RAW_SetParam cmd 0x2b -> ARC_Turbo_RAW/HDR_Process) writes to it and faults
        # (intermittent SIGSEGV) or bails, leaving the saved photo soft+GREEN (zero chroma).
        # ALL libs in the path (libarcsoft_turbo_raw/hdr, libarc.ion, libmpbase) are byte-identical
        # to stock and every CPU allocator returns valid buffers -> NOT a blob field bug; it's a
        # buffer-provisioning/mapping (QNN-DSP rpcmem / gralloc HW-buffer) env gap that stock maps
        # and the port doesn't. No clean byte-patch exists for that. Pragmatic workaround: NOP the
        # turboHdrProcessV2 call so Normal/Auto saves the pre-turbo APS result -> CORRECT COLOR,
        # no crash (but soft, since turbo-HDR's multi-frame detail is skipped). The next insn
        # reloads x0 ([x20,#0x208]) so the skipped call's return value is unused -> safe to NOP.
        #   workRoutine: ldr x8,[sp,#0x3a0]; mov x9,x0; mov x0,x8; blr x9; ldr x0,[x20,#0x208]
        #   blr x9 (d63f0120) -> nop (d503201f).  Remove this if turbo-HDR is fixed properly.
        .binary_regex_replace(
            b'\xe8\xd3\x41\xf9\xe9\x03\x00\xaa\xe0\x03\x08\xaa\x20\x01\x3f\xd6',
            b'\xe8\xd3\x41\xf9\xe9\x03\x00\xaa\xe0\x03\x08\xaa\x1f\x20\x03\xd5',
        )
        # APS turbo soft/GREEN/crash is now fixed at RUNTIME by libapsfixup.so
        # (device/oneplus/dodge/apsfixup), loaded via this DT_NEEDED. Root cause: the port's
        # gralloc/IMapper reports a wrong plane layout for the 4096x3072 P010 capture-output
        # buffer, so the byte-identical ArcSoft/Algo blobs build a garbage chroma plane. The
        # interposer corrects, at runtime: (1) ARC_Turbo_RAW_Process output struct chroma plane
        # ptr = luma + Ysize (was align_up(luma,0) = 4GB), (2) chroma pitch = Y stride (was 0),
        # (3) p010LSB2MSBNeon length so w4*w5*1.5 == buffer (full Y+UV, no overrun). Turbo runs
        # normally -> sharp + correct color.
        .add_needed('libapsfixup.so'),

    'odm/lib64/libEISLive.so': blob_fixup()
        .clear_symbol_version('AHardwareBuffer_acquire')
        .clear_symbol_version('AHardwareBuffer_allocate')
        .clear_symbol_version('AHardwareBuffer_describe')
        .clear_symbol_version('AHardwareBuffer_lock')
        .clear_symbol_version('AHardwareBuffer_lockPlanes')
        .clear_symbol_version('AHardwareBuffer_release')
        .clear_symbol_version('AHardwareBuffer_unlock'),
}  # fmt: skip

namespace_imports = [
    'vendor/oplus/camera/camera',
    'vendor/oneplus/dodge',
    'vendor/oneplus/sm8750-common',
    'hardware/oplus_dodge',
]

module = ExtractUtilsModule(
    'camera',
    'oplus/camera',
    device_rel_path='vendor/oplus/camera',
    blob_fixups=blob_fixups,
    lib_fixups=lib_fixups,
    namespace_imports=namespace_imports,
)

if __name__ == '__main__':
    utils = ExtractUtils.device(module)
    utils.run()
