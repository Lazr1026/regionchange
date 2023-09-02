#!/usr/bin/env python
# encoding: utf-8

# may or may not be inspired by plutoo's ctrrpc
import codecs
import errno
import os
import re
import socket
import struct
import sys
from time import sleep

def buffer(size):
    return bytearray([0x00] * size)

def copy_string(buffer, s, offset):
    s += '\0'
    buffer[offset : (offset + len(s))] = bytearray(s, 'ascii')

def copy_word(buffer, w, offset):
    buffer[offset : (offset + 4)] = struct.pack('>I', w)

def get_string(buffer, offset):
    s = buffer[offset:]
    if b'\x00' in s:
        return s[:s.index(b'\x00')].decode('utf-8')
    return s.decode('utf-8')

class WupClient:
    s = None

    def __init__(self, ip, port=1337):
        self.s = socket.socket()
        self.s.connect((ip, port))
        self.fsa_handle = self.open('/dev/fsa', 0)
        self.cwd = '/vol/storage_mlc01'

    def __del__(self):
        self.FSA_Unmount(self.fsa_handle, '/vol/storage_sdcard', 2)
        self.close(self.fsa_handle)

    # fundamental comms
    def send(self, command, data):
        request = struct.pack('>I', command) + data

        self.s.send(request)
        response = self.s.recv(0x600)

        ret = struct.unpack('>I', response[:4])[0]
        return (ret, response[4:])

    # core commands
    def read(self, addr, len):
        data = struct.pack('>II', addr, len)
        ret, data = self.send(1, data)
        if ret == 0:
            return data
        print('read error : %08X' % ret)

    def send_and_exit(self, command, data):
        request = struct.pack('>I', command) + data
        self.s.send(request)
        self.s.close()
        self.s = None
        self.fsa_handle = None
        exit()

    def write(self, addr, data):
        data = struct.pack('>I', addr) + data
        ret, data = self.send(0, data)
        if ret == 0:
            return ret
        print('write error : %08X' % ret)

    def svc(self, svc_id, arguments):
        data = struct.pack('>I', svc_id)
        for a in arguments:
            data += struct.pack('>I', a)
        ret, data = self.send(2, data)
        if ret == 0:
            return struct.unpack('>I', data)[0]
        print('svc error : %08X' % ret)

    def svc_and_exit(self, svc_id, arguments):
        data = struct.pack('>I', svc_id)
        for a in arguments:
            data += struct.pack('>I', a)
        self.send_and_exit(2, data)

    def kill(self):
        ret, _ = self.send(3, bytearray())
        return ret

    def memcpy(self, dst, src, len):
        data = struct.pack('>III', dst, src, len)
        ret, data = self.send(4, data)
        if ret == 0:
            return ret
        print('memcpy error : %08X' % ret)

    def repeatwrite(self, dst, val, n):
        data = struct.pack('>III', dst, val, n)
        ret, data = self.send(5, data)
        if ret == 0:
            return ret
        print('repeatwrite error : %08X' % ret)

    # derivatives
    def alloc(self, size, align=None):
        if size == 0:
            return 0
        if align is None:
            return self.svc(0x27, [0xCAFF, size])
        return self.svc(0x28, [0xCAFF, size, align])

    def free(self, address):
        if address == 0:
            return 0
        return self.svc(0x29, [0xCAFF, address])

    def load_buffer(self, b, align=None):
        if len(b) == 0:
            return 0
        address = self.alloc(len(b), align)
        self.write(address, b)
        return address

    def load_string(self, s, align=None):
        return self.load_buffer(bytearray(s + '\0', 'ascii'), align)

    def open(self, device, mode):
        address = self.load_string(device)
        handle = self.svc(0x33, [address, mode])
        self.free(address)
        return handle

    def close(self, handle):
        return self.svc(0x34, [handle])

    def ioctl(self, handle, cmd, inbuf, outbuf_size):
        in_address = self.load_buffer(inbuf)
        out_data = None
        if outbuf_size > 0:
            out_address = self.alloc(outbuf_size)
            ret = self.svc(0x38, [handle, cmd, in_address, len(inbuf), out_address, outbuf_size])
            out_data = self.read(out_address, outbuf_size)
            self.free(out_address)
        else:
            ret = self.svc(0x38, [handle, cmd, in_address, len(inbuf), 0, 0])
        self.free(in_address)
        return (ret, out_data)

    def iovec(self, vecs):
        data = bytearray()
        for (a, s) in vecs:
            data += struct.pack('>III', a, s, 0)
        return self.load_buffer(data)

    def ioctlv(self, handle, cmd, inbufs, outbuf_sizes, inbufs_ptr=[], outbufs_ptr=[]):
        inbufs = [(self.load_buffer(b, 0x40), len(b)) for b in inbufs]
        outbufs = [(self.alloc(s, 0x40), s) for s in outbuf_sizes]
        iovecs = self.iovec(inbufs + inbufs_ptr + outbufs_ptr + outbufs)
        out_data = []
        ret = self.svc(0x39, [handle, cmd, len(inbufs + inbufs_ptr), len(outbufs + outbufs_ptr), iovecs])
        for (a, s) in outbufs:
            out_data += [self.read(a, s)]
        for (a, _) in (inbufs + outbufs):
            self.free(a)
        self.free(iovecs)
        return (ret, out_data)

    # fsa
    def FSA_Mount(self, handle, device_path, volume_path, flags):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, device_path, 0x0004)
        copy_string(inbuffer, volume_path, 0x0284)
        copy_word(inbuffer, flags, 0x0504)
        (ret, _) = self.ioctlv(handle, 0x01, [inbuffer, bytearray()], [0x293])
        return ret

    def FSA_Unmount(self, handle, path, flags):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_word(inbuffer, flags, 0x284)
        (ret, _) = self.ioctl(handle, 0x02, inbuffer, 0x293)
        return ret

    def FSA_RawOpen(self, handle, device):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, device, 0x4)
        (ret, data) = self.ioctl(handle, 0x6A, inbuffer, 0x293)
        return (ret, struct.unpack('>I', data[4:8])[0])

    def FSA_OpenDir(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        (ret, data) = self.ioctl(handle, 0x0A, inbuffer, 0x293)
        return (ret, struct.unpack('>I', data[4:8])[0])

    def FSA_ReadDir(self, handle, dir_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, dir_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x0B, inbuffer, 0x293)
        data = bytearray(data[4:])
        unk = data[:0x64]
        if ret == 0:
            return (ret, {'name': get_string(data, 0x64), 'is_file': (unk[0] & 128) != 128, 'unk': unk})
        return (ret, None)

    def FSA_CloseDir(self, handle, dir_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, dir_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x0D, inbuffer, 0x293)
        return ret

    def FSA_OpenFile(self, handle, path, mode):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_string(inbuffer, mode, 0x284)
        (ret, data) = self.ioctl(handle, 0x0E, inbuffer, 0x293)
        return (ret, struct.unpack('>I', data[4:8])[0])

    def FSA_MakeDir(self, handle, path, flags):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x4)
        copy_word(inbuffer, flags, 0x284)
        (ret, _) = self.ioctl(handle, 0x07, inbuffer, 0x293)
        return ret

    def FSA_ReadFile(self, handle, file_handle, size, cnt):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, size, 0x08)
        copy_word(inbuffer, cnt, 0x0C)
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x0F, [inbuffer], [size * cnt, 0x293])
        return (ret, data[0])

    def FSA_WriteFile(self, handle, file_handle, data):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, 1, 0x08) # size
        copy_word(inbuffer, len(data), 0x0C) # cnt
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x10, [inbuffer, data], [0x293])
        return (ret)

    def FSA_ReadFilePtr(self, handle, file_handle, size, cnt, ptr):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, size, 0x08)
        copy_word(inbuffer, cnt, 0x0C)
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x0F, [inbuffer], [0x293], [], [(ptr, size*cnt)])
        return (ret, data[0])

    def FSA_WriteFilePtr(self, handle, file_handle, size, cnt, ptr):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, size, 0x08)
        copy_word(inbuffer, cnt, 0x0C)
        copy_word(inbuffer, file_handle, 0x14)
        (ret, data) = self.ioctlv(handle, 0x10, [inbuffer], [0x293], [(ptr, size*cnt)], [])
        return (ret)

    def FSA_GetStatFile(self, handle, file_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, file_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x14, inbuffer, 0x64)
        return (ret, struct.unpack('>IIIIIIIIIIIIIIIIIIIIIIIII', data))

    def FSA_CloseFile(self, handle, file_handle):
        inbuffer = buffer(0x520)
        copy_word(inbuffer, file_handle, 0x4)
        (ret, data) = self.ioctl(handle, 0x15, inbuffer, 0x293)
        return ret

    def FSA_ChangeMode(self, handle, path, mode, mask=0x777):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x0004)
        copy_word(inbuffer, mode, 0x0284)
        copy_word(inbuffer, mask, 0x0288)
        (ret, _) = self.ioctl(handle, 0x20, inbuffer, 0x293)
        return ret

    def FSA_Rename(self, handle, oldpath, newpath):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, oldpath, 0x4)
        copy_string(inbuffer, newpath, 0x284)
        (ret, _) = self.ioctl(handle, 0x09, inbuffer, 0x293)
        return ret

    def FSA_Remove(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x04)
        (ret, _) = self.ioctl(handle, 0x08, inbuffer, 0x293)
        return ret

    def FSA_FlushVolume(self, handle, path):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x04)
        (ret, _) = self.ioctl(handle, 0x1B, inbuffer, 0x293)
        return ret

    def FSA_Format(self, handle, device_path, filesystem, flags):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, device_path, 0x0004)
        copy_string(inbuffer, filesystem,  0x0284)
        copy_word(inbuffer, flags, 0x028F)
        (ret, _) = self.ioctl(handle, 0x69, inbuffer, 0x293)
        return ret

    def FSA_GetInfoByQuery(self, handle, path, type):
        inbuffer = buffer(0x520)
        copy_string(inbuffer, path, 0x0004)
        copy_word(inbuffer, type, 0x0284)
        (ret, data) = self.ioctl(handle, 0x18, inbuffer, 0x64)
        return (ret, struct.unpack('>IIIIIIIIIIIIIIIIIIIIIIIII', data))

    # mcp
    def MCP_InstallGetInfo(self, handle, path):
        inbuffer = buffer(0x27F)
        copy_string(inbuffer, path, 0x0)
        (ret, data) = self.ioctlv(handle, 0x80, [inbuffer], [0x16])
        return (ret, struct.unpack('>IIIIIH', data[0]))

    def MCP_Install(self, handle, path):
        inbuffer = buffer(0x27F)
        copy_string(inbuffer, path, 0x0)
        (ret, _) = self.ioctlv(handle, 0x81, [inbuffer], [])
        return ret

    def MCP_InstallGetProgress(self, handle):
        (ret, data) = self.ioctl(handle, 0x82, [], 0x24)
        return (ret, struct.unpack('>IIIIIIIII', data))

    def MCP_DeleteTitle(self, handle, path, flush):
        inbuffer = buffer(0x38)
        copy_string(inbuffer, path, 0x0)
        inbuffer2 = buffer(0x4)
        copy_word(inbuffer2, flush, 0x0)
        (ret, _) = self.ioctlv(handle, 0x83, [inbuffer, inbuffer2], [])
        return ret

    def MCP_CopyTitle(self, handle, path, dst_device_id, flush):
        inbuffer = buffer(0x27F)
        copy_string(inbuffer, path, 0x0)
        inbuffer2 = buffer(0x4)
        copy_word(inbuffer2, dst_device_id, 0x0)
        inbuffer3 = buffer(0x4)
        copy_word(inbuffer3, flush, 0x0)
        (ret, _) = self.ioctlv(handle, 0x85, [inbuffer, inbuffer2, inbuffer3], [])
        return ret

    def MCP_InstallSetTargetDevice(self, handle, device):
        inbuffer = buffer(0x4)
        copy_word(inbuffer, device, 0x0)
        (ret, _) = self.ioctl(handle, 0x8D, inbuffer, 0)
        return ret

    def MCP_InstallSetTargetUsb(self, handle, device):
        inbuffer = buffer(0x4)
        copy_word(inbuffer, device, 0x0)
        (ret, _) = self.ioctl(handle, 0xF1, inbuffer, 0)
        return ret

    # syslog (tmp)
    def dump_syslog(self):
        syslog_address = struct.unpack('>I', self.read(0x05095ECC, 4))[0] + 0x10
        block_size = 0x400
        for i in range(0, 0x40000, block_size):
            data = self.read(syslog_address + i, 0x400)
            # if 0 in data:
            #     print(data[:data.index(0)].decode('ascii'))
            #     break
            # else:
            print(data.decode('ascii'))

    def mkdir(self, path, flags):
        if path[0] != '/':
            path = self.cwd + '/' + path
        ret = self.FSA_MakeDir(self.fsa_handle, path, flags)
        if ret == 0:
            return 0
        print('mkdir error (%s, %08X)' % (path, ret))
        return ret

    def chmod(self, filename, flags):
        if filename[0] != '/':
            filename = self.cwd + '/' + filename
        ret = self.FSA_ChangeMode(self.fsa_handle, filename, flags)
        print('chmod returned : ' + hex(ret))

    def cd(self, path):
        if path[0] != '/' and self.cwd[0] == '/':
            return self.cd(self.cwd + '/' + path)
        ret, dir_handle = self.FSA_OpenDir(self.fsa_handle, path if path is not None else self.cwd)
        if ret == 0:
            self.cwd = path
            self.FSA_CloseDir(self.fsa_handle, dir_handle)
            return 0
        print('cd error : path does not exist (%s)' % (path))
        return -1

    def ls(self, path=None, return_data=False):
        if path is not None and path[0] != '/':
            path = self.cwd + '/' + path
        ret, dir_handle = self.FSA_OpenDir(self.fsa_handle, path if path is not None else self.cwd)
        if ret != 0x0:
            print('opendir error : ' + hex(ret))
            return [] if return_data else None
        entries = []
        while True:
            ret, data = self.FSA_ReadDir(self.fsa_handle, dir_handle)
            if ret != 0:
                break
            if not(return_data):
                if data['is_file']:
                    print('     %s' % data['name'])
                else:
                    print('     %s/' % data['name'])
            else:
                entries += [data]
        ret = self.FSA_CloseDir(self.fsa_handle, dir_handle)
        return entries if return_data else None

    def dldir(self, path):
        if path[0] != '/':
            path = self.cwd + '/' + path
        entries = self.ls(path, True)
        for e in entries:
            if e['is_file']:
                print(e['name'])
                self.dl(path + '/' + e['name'],path[1:])
            else:
                print(e['name'] + '/')
                self.dldir(path + '/' + e['name'])

    def cpdir(self, srcpath, dstpath):
        entries = self.ls(srcpath, True)
        q = [(srcpath, dstpath, e) for e in entries]
        while len(q) > 0:
            _srcpath, _dstpath, e = q.pop()
            _srcpath += '/' + e['name']
            _dstpath += '/' + e['name']
            if e['is_file']:
                print(e['name'])
                self.cp(_srcpath, _dstpath)
            else:
                self.mkdir(_dstpath, 0x600)
                entries = self.ls(_srcpath, True)
                q += [(_srcpath, _dstpath, e) for e in entries]

    def pwd(self):
        return self.cwd

    def cp(self, filename_in, filename_out):
        ret, in_file_handle = self.FSA_OpenFile(self.fsa_handle, filename_in, 'r')
        if ret != 0x0:
            print('cp error : could not open ' + filename_in)
            return
        ret, out_file_handle = self.FSA_OpenFile(self.fsa_handle, filename_out, 'w')
        if ret != 0x0:
            print('cp error : could not open ' + filename_out)
            return
        block_size = 0x10000
        buffer = self.alloc(block_size, 0x40)
        k = 0
        while True:
            ret, _ = self.FSA_ReadFilePtr(self.fsa_handle, in_file_handle, 0x1, block_size, buffer)
            k += ret
            ret = self.FSA_WriteFilePtr(self.fsa_handle, out_file_handle, 0x1, ret, buffer)
            sys.stdout.write(hex(k) + '\r'); sys.stdout.flush();
            if ret < block_size:
                break
        self.free(buffer)
        ret = self.FSA_CloseFile(self.fsa_handle, out_file_handle)
        ret = self.FSA_CloseFile(self.fsa_handle, in_file_handle)

    def df(self, filename_out, src, size):
        ret, out_file_handle = self.FSA_OpenFile(self.fsa_handle, filename_out, 'w')
        if ret != 0x0:
            print('df error : could not open ' + filename_out)
            return
        block_size = 0x10000
        buffer = self.alloc(block_size, 0x40)
        k = 0
        while k < size:
            cur_size = min(size - k, block_size)
            self.memcpy(buffer, src + k, cur_size)
            k += cur_size
            ret = self.FSA_WriteFilePtr(self.fsa_handle, out_file_handle, 0x1, cur_size, buffer)
            sys.stdout.write(hex(k) + ' (%f) ' % (float(k * 100) / size) + '\r'); sys.stdout.flush();
        self.free(buffer)
        ret = self.FSA_CloseFile(self.fsa_handle, out_file_handle)

    def dl_buf(self, filename, show_progress = True):
        if filename[0] != '/':
            filename = self.cwd + '/' + filename
        ret, file_handle = self.FSA_OpenFile(self.fsa_handle, filename, 'r')
        if ret != 0x0:
            print('dl error : could not open ' + filename)
            return None
        buf = bytearray()
        block_size = 0x400
        while True:
            ret, data = self.FSA_ReadFile(self.fsa_handle, file_handle, 0x1, block_size)
            buf += data[:ret]
            if show_progress:
                sys.stdout.write(hex(len(buf)) + '\r')
                sys.stdout.flush()
            if ret < block_size:
                break
        self.FSA_CloseFile(self.fsa_handle, file_handle)
        return buf

    def dl(self, filename, directorypath=None, local_filename=None):
        buf = self.dl_buf(filename)
        if buf is None:
            return -1
        if local_filename is None:
            if '/' in filename:
                local_filename = filename[[i for i, x in enumerate(filename) if x == '/'][-1]+1:]
            else:
                local_filename = filename
        if directorypath is None:
            open(local_filename, 'wb').write(buf)
        else:
            dir_path = os.path.dirname(os.path.abspath(sys.argv[0])).replace('\\','/')
            fullpath = dir_path + '/' + directorypath + '/'
            fullpath = fullpath.replace('//','/')
            mkdir_p(fullpath)
            open(fullpath + local_filename, 'wb').write(buf)
        return 0

    def mkdir_p(path):
        try:
            os.makedirs(path)
        except OSError as exc:  # Python >2.5
            if exc.errno == errno.EEXIST and os.path.isdir(path):
                pass
            raise exc

    def fr(self, filename, offset, size):
        if filename[0] != '/':
            filename = self.cwd + '/' + filename
        ret, file_handle = self.FSA_OpenFile(self.fsa_handle, filename, 'r')
        if ret != 0x0:
            print('fr error : could not open ' + filename)
            return
        buffer = bytearray()
        block_size = 0x400
        while True:
            ret, data = self.FSA_ReadFile(self.fsa_handle, file_handle, 0x1, block_size if (block_size < size) else size)
            buffer += data[:ret]
            sys.stdout.write(hex(len(buffer)) + '\r'); sys.stdout.flush();
            if len(buffer) >= size:
                break
        ret = self.FSA_CloseFile(self.fsa_handle, file_handle)
        return buffer

    def fw(self, filename, offset, buffer):
        if filename[0] != '/':
            filename = self.cwd + '/' + filename
        ret, file_handle = self.FSA_OpenFile(self.fsa_handle, filename, 'r+')
        if ret != 0x0:
            print('fw error : could not open ' + filename)
            return
        block_size = 0x400
        k = 0
        while True:
            cur_size = min(len(buffer) - k, block_size)
            if cur_size <= 0:
                break
            sys.stdout.write(hex(k) + '\r'); sys.stdout.flush();
            ret = self.FSA_WriteFile(self.fsa_handle, file_handle, buffer[k:(k+cur_size)])
            k += cur_size
        ret = self.FSA_CloseFile(self.fsa_handle, file_handle)

    def stat(self, filename):
        if filename[0] != '/':
            filename = self.cwd + '/' + filename
        ret, file_handle = self.FSA_OpenFile(self.fsa_handle, filename, 'r')
        if ret != 0x0:
            print('stat error : could not open ' + filename)
            return
        (ret, stats) = self.FSA_GetStatFile(self.fsa_handle, file_handle)
        if ret != 0x0:
            print('stat error : ' + hex(ret))
        else:
            print('flags: ' + hex(stats[1]))
            print('mode: ' + hex(stats[2]))
            print('owner: ' + hex(stats[3]))
            print('group: ' + hex(stats[4]))
            print('size: ' + hex(stats[5]))
        ret = self.FSA_CloseFile(self.fsa_handle, file_handle)

    def askyesno(self):
        while True:
            choice = input().lower()
            if choice in ('yes', 'ye', 'y'):
               return True
            elif choice in ('no','n', ''):
               return False
            else:
               print("Please respond with 'y' or 'n'")

    def rm(self, filename):
        if filename[0] != '/':
            filename = self.cwd + '/' + filename
        ret, file_handle = self.FSA_OpenFile(self.fsa_handle, filename, 'r')
        if ret != 0x0:
            print('rm error : could not open ' + filename + ' (' + hex(ret) + ')')
            return
        self.FSA_CloseFile(self.fsa_handle, file_handle)
        #print('WARNING: REMOVING A FILE CAN BRICK YOUR CONSOLE, ARE YOU SURE (Y/N)?')
        #if self.askyesno() == True:
        ret = self.FSA_Remove(self.fsa_handle, filename)
        print('rm : ' + hex(ret))

    # Credits to Nightkingale at https://nightkingale.com/posts/the-downgrade-of-doom
    def rmdir(self, path):
        fsa_handle = self.fsa_handle
        if path[0] != '/':
            path = self.cwd + '/' + path
        ret, dir_handle = self.FSA_OpenDir(fsa_handle, path)
        if ret != 0x0:
            print('rmdir error : could not open ' + path + ' (' + hex(ret) + ')')
            return
        self.FSA_CloseDir(fsa_handle, dir_handle)
        if len(self.ls(path, True)) != 0:
            entries = self.ls(path, True)
            for e in entries:
                if e['is_file']:
                    print('deleting: ' + e['name'])
                    self.rm(path + '/' + e['name'])
                else:
                    print('deleting: ' + e['name'] + '/')
                    self.rmdir(path + '/' + e['name'])
        ret = self.FSA_Remove(fsa_handle, path)
        print('rmdir : ' + hex(ret))

    def mv(self, srcpath, dstpath):
        if srcpath[0] != '/':
            srcpath = self.cwd + '/' + srcpath
        if dstpath[0] != '/':
            dstpath = self.cwd + '/' + dstpath
        print('WARNING: MOVING A FILE OR FOLDER CAN BRICK YOUR CONSOLE, ARE YOU SURE (Y/N)?')
        if self.askyesno() == True:
            ret = self.FSA_Rename(self.fsa_handle, srcpath, dstpath)
            if ret == 0x0:
                print('moved ' + srcpath + ' to ' + dstpath)
            else:
                print('moving ' + srcpath + ' to ' + dstpath + ' failed : ' + hex(ret))
        else:
            print('mv aborted')

    def up(self, local_filename, filename=None):
        if filename is None:
            if '/' in local_filename:
                filename = local_filename[[i for i, x in enumerate(local_filename) if x == '/'][-1]+1:]
            else:
                filename = local_filename
        if filename[0] != '/':
            filename = self.cwd + '/' + filename
        f = open(local_filename, 'rb')
        ret, file_handle = self.FSA_OpenFile(self.fsa_handle, filename, 'w')
        if ret != 0x0:
            print('up error : could not open ' + filename)
            return
        progress = 0
        block_size = 0x400
        while True:
            data = f.read(block_size)
            ret = self.FSA_WriteFile(self.fsa_handle, file_handle, data)
            progress += len(data)
            sys.stdout.write(hex(progress) + '\r'); sys.stdout.flush();
            if len(data) < block_size:
                break
        ret = self.FSA_CloseFile(self.fsa_handle, file_handle)

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        raise exc

def mount_sd():
    ret = w.FSA_Mount(w.fsa_handle, '/dev/sdcard01', '/vol/storage_sdcard', 2)
    print(hex(ret))

def format_sd():
    ret = w.FSA_Format(w.fsa_handle, '/dev/sdcard01', 'fat', 0)
    print(hex(ret))

def unmount_mlc():
    ret = w.FSA_Unmount(w.fsa_handle, '/vol/storage_mlc01', 2)
    print(hex(ret))

def mount_mlc():
    ret = w.FSA_Mount(w.fsa_handle, '/dev/mlc01', '/vol/storage_mlc01', 2)
    print(hex(ret))

def format_mlc():
    ret = w.FSA_Format(w.fsa_handle, '/dev/mlc01', 'wfs', 0)
    print(hex(ret))

def unmount_sd():
    ret = w.FSA_Unmount(w.fsa_handle, '/vol/storage_sdcard', 2)
    print(hex(ret))

def mount_slccmpt01():
    ret = w.FSA_Mount(w.fsa_handle, '/dev/slccmpt01', '/vol/storage_slccmpt01', 2)
    print(hex(ret))

def unmount_slccmpt01():
    ret = w.FSA_Unmount(w.fsa_handle, '/vol/storage_slccmpt01', 2)
    print(hex(ret))

def mount_odd_content():
    ret = w.FSA_Mount(w.fsa_handle, '/dev/odd03', '/vol/storage_odd_content', 2)
    print(hex(ret))

def unmount_odd_content():
    ret = w.FSA_Unmount(w.fsa_handle, '/vol/storage_odd_content', 2)
    print(hex(ret))

def mount_odd_update():
    ret = w.FSA_Mount(w.fsa_handle, '/dev/odd02', '/vol/storage_odd_update', 2)
    print(hex(ret))

def unmount_odd_update():
    ret = w.FSA_Unmount(w.fsa_handle, '/vol/storage_odd_update', 2)
    print(hex(ret))

def mount_odd_tickets():
    ret = w.FSA_Mount(w.fsa_handle, '/dev/odd01', '/vol/storage_odd_tickets', 2)
    print(hex(ret))

def unmount_odd_tickets():
    ret = w.FSA_Unmount(w.fsa_handle, '/vol/storage_odd_tickets', 2)
    print(hex(ret))

def get_tik_keys():
    base_path = '/vol/system/rights/ticket/apps'
    entries = w.ls(base_path, True)
    #parse subfolder contents to get tik location
    tikFiles = []
    for e in entries:
        if not e['is_file']:
            path = base_path + '/' + e['name']
            subentries = w.ls(path, True)
            for se in subentries:
                if se['is_file'] and se['name'].endswith('.tik'):
                    tikFiles.append(path + '/' + se['name'])
    #go through all tiks
    tikList = []
    for tikF in tikFiles:
        tikContent = w.dl_buf(tikF, False)
        if tikContent is None:
            continue
        checkTik = True
        tikP = 0
        while checkTik == True:
            checkTik = False
            curTik = tikContent[tikP:]
            if(curTik[0:4] != b'\x00\x01\x00\x04'):
                print('Unhandled tik start at %i with ticket %s!' % (tikP, tikF))
                break
            titleId = codecs.encode(curTik[0x1DC:0x1E4], 'hex').decode()
            titleKey = codecs.encode(curTik[0x1BF:0x1CF], 'hex').decode()
            tikFprint = tikF[tikF.rfind('apps/')+5:]
            tikList.append(titleId + ' ' + titleKey + ' (' + tikFprint + ' @ ' + hex(tikP) + ')')
            if(len(curTik) > 0x354):
                if(curTik[0x2B0:0x2B2] == b'\x00\x00' and curTik[0x2B8:0x2BC] == b'\x00\x01\x00\x04'):
                    tikP += 0x2B8
                    checkTik = True
                elif(curTik[0x2B0:0x2B2] == b'\x00\x01' and curTik[0x350:0x354] == b'\x00\x01\x00\x04'):
                    tikP += 0x350
                    checkTik = True
                else:
                    print('Unhandled packed tik at %i with ticket %s!' % (tikP, tikF))
    #print out all sorted and unique tiks
    uniqueTiks = sorted(set(tikList))
    print('Found %i unique tickets' % len(uniqueTiks))
    for tikCnt in uniqueTiks:
        print(tikCnt)

#path=root folder of installed/extracted title, only works if title is deleted
#on the destination device beforehand; path can also be a sd card location!
def copy_title(path, installToUsb=0, flush=0):
    mcp_handle = w.open('/dev/mcp', 0)
    print(hex(mcp_handle))

    ret = w.MCP_CopyTitle(mcp_handle, path, installToUsb, flush)
    print(hex(ret))

    ret = w.close(mcp_handle)
    print(hex(ret))

#path=path to sd card folder on device root
def install_title(path, installToUsb=0):
    mcp_handle = w.open('/dev/mcp', 0)
    print(hex(mcp_handle))

    ret, data = w.MCP_InstallGetInfo(mcp_handle, '/vol/storage_sdcard/' + path)
    print('install info : ' + hex(ret), [hex(v) for v in data])
    if ret != 0:
        ret = w.close(mcp_handle)
        print(hex(ret))
        return

    ret = w.MCP_InstallSetTargetDevice(mcp_handle, installToUsb)
    print('install set target device : ' + hex(ret))
    if ret != 0:
        ret = w.close(mcp_handle)
        print(hex(ret))
        return

    ret = w.MCP_InstallSetTargetUsb(mcp_handle, installToUsb)
    print('install set target usb : ' + hex(ret))
    if ret != 0:
        ret = w.close(mcp_handle)
        print(hex(ret))
        return

    ret = w.MCP_Install(mcp_handle, '/vol/storage_sdcard/' + path)
    print('install : ' + hex(ret))

    ret = w.close(mcp_handle)
    print(hex(ret))

#path=full path, for example '/vol/storage_mlc01/usr/title/00050000/10179C00'
def delete_title(path, flush = 0):
    mcp_handle = w.open('/dev/mcp', 0)
    print(hex(mcp_handle))

    ret = w.MCP_DeleteTitle(mcp_handle, path, flush)
    print('delete title : ' + hex(ret))

    ret = w.close(mcp_handle)
    print(hex(ret))

def ios_shutdown():
    w.svc_and_exit(0x72, [0])

def ios_reset():
    w.svc_and_exit(0x72, [1])

def get_nim_status():
    nim_handle = w.open('/dev/nim', 0)
    print(hex(nim_handle))

    inbuffer = buffer(0x80)
    (ret, data) = w.ioctlv(nim_handle, 0x00, [inbuffer], [0x80])

    print(hex(ret), ''.join('%02X' % v for v in data[0]))

    ret = w.close(nim_handle)
    print(hex(ret))

def read_and_print(adr, size):
    data = w.read(adr, size)
    data = struct.unpack('>%dI' % (len(data) // 4), data)
    for i in range(0, len(data), 4):
        print(' '.join('%08X' % v for v in data[i:i+4]))

def read_and_dump(adr, size):
    f = open('dump.bin', 'wb')
    for i in range(0, size, 1024):
        data = w.read(adr+i, 1024)
        f.write(data)
    f.close()

def flush_mlc():
    ret = w.FSA_FlushVolume(w.fsa_handle, '/vol/storage_mlc01')
    print(hex(ret))

######################### REGION CHANGE ###############################
def re_findall(obj, regex, only_first=True):
    if re.search(regex, obj):
        f = re.findall(regex, obj)
        if isinstance(f, (list, tuple)) and len(f) > 0:
            if only_first:
                return f[0]
            return f

def re_sub(obj, regex, new):
    return re.sub(regex, new, obj)

def extract_sys_prod(obj):
    sys_prod = {}
    for f in (
        'version',
        'eeprom_version',
        'product_area',
        'game_region',
        'ntsc_pal',
        '5ghz_country_code',
        '5ghz_country_code_revision',
        'code_id',
        'serial_id',
        'model_number',
    ):
        if (_ := re_findall(obj, f'<{f}[^/]+</{f}>')) is not None:
            sys_prod[f] = {
                'value':re_findall(_, f'">(.*)</{f}>'),
                'type':re_findall(_, r'type="(.*)" l'),
                'length':re_findall(_, r'length="(.*)" a'),
                'access':re_findall(_, r'access="(.*)">'),
            }
    return sys_prod

def read_file(path, mode='r+'):
    try:
        with open(path, mode) as f:
            return f.read()
    except:
        pass

def write_file(obj, path, mode='w+'):
    try:
        with open(path, mode) as f:
            return f.write(obj)
    except:
        pass

def create_wupclient(ip_address, max_retries=3):
    for i in range(max_retries):
        try:
            w = WupClient(ip_address)
        except TimeoutError:
            sleep(1)
        else:
            return w

def ask_and_boolify(msg, values=('1', 'ok', 'on', 't', 'true', 'yes', 'ye', 'y', '')):
    return input(msg).lower() in values

SYSTEM_TITLES = {
    'JPN':[
        '00050010-10040000',
        '00050010-10041000',
        '00050010-10043000',
        '00050010-10044000',
        '00050010-10045000',
        '00050010-10047000',
        '00050010-10048000',
        '00050010-10049000',
        '00050010-1004a000',
        '00050010-1004b000',
        '00050010-1004c000',
        '00050010-1004d000',
        '00050010-1004e000',
        '00050010-1005a000',
        '00050010-10062000',
        '0005001b-10059000',
        '0005001b-10067000',
        '0005001b-10069000',
        '00050030-10010009',
        '00050030-1001000a',
        '00050030-1001100a',
        '00050030-100110ff',
        '00050030-1001200a',
        '00050030-1001300a',
        '00050030-1001400a',
        '00050030-1001500a',
        '00050030-1001600a',
        '00050030-10017009',
        '00050030-1001700a',
        '00050030-1001800a',
        '00050030-1001900a',
        '00050030-1006d00a'
    ],
    'USA':[
        '00050010-10040100',
        '00050010-10041100',
        '00050010-10043100',
        '00050010-10044100',
        '00050010-10045100',
        '00050010-10047100',
        '00050010-10048100',
        '00050010-10049100',
        '00050010-1004a100',
        '00050010-1004b100',
        '00050010-1004c100',
        '00050010-1004d100',
        '00050010-1004e100',
        '00050010-1005a100',
        '00050010-10062100',
        '0005001b-10059100',
        '0005001b-10067100',
        '0005001b-10069100',
        '00050030-10010109',
        '00050030-10011109',
        '00050030-1001010a',
        '00050030-1001110a',
        '00050030-100111ff',
        '00050030-1001210a',
        '00050030-1001310a',
        '00050030-1001410a',
        '00050030-1001510a',
        '00050030-1001610a',
        '00050030-10017109',
        '00050030-1001710a',
        '00050030-1001810a',
        '00050030-1001910a',
        '00050030-1006d10a'
    ],
    'EUR':[
        '00050010-10040200',
        '00050010-10041200',
        '00050010-10043200',
        '00050010-10044200',
        '00050010-10045200',
        '00050010-10047200',
        '00050010-10048200',
        '00050010-10049200',
        '00050010-1004a200',
        '00050010-1004b200',
        '00050010-1004c200',
        '00050010-1004d200',
        '00050010-1004e200',
        '00050010-1005a200',
        '00050010-10062200',
        '0005001b-10059200',
        '0005001b-10067200',
        '0005001b-10069200',
        '00050030-10010209',
        '00050030-1001020a',
        '00050030-1001120a',
        '00050030-100112ff',
        '00050030-1001220a',
        '00050030-1001320a',
        '00050030-1001420a',
        '00050030-1001520a',
        '00050030-1001620a',
        '00050030-10017209',
        '00050030-1001720a',
        '00050030-1001820a',
        '00050030-1001920a',
        '00050030-1006d20a'
    ]
}

class RegionChanger(object):
    IP_REGEX = re.compile(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}')
    MODEL_NUMBER_REGEX = re.compile(r'^(?P<wup>[A-Z]{3}+)-(?P<id>[0-9]{0,3}+)?(\()?(?P<region>[0-9]{0,3}+)?(\))?')

    REGION_HAX = 119

    SYS_PROD_PATH = '/vol/system/config/sys_prod.xml'
    DRC_CONFIG_PATH = '/vol/system/proc/prefs/DRCCfg.xml'
    COOLBOOT_PATH = '/vol/system/config/system.xml'

    #def __init__(self, *args, **kwargs): pass

    @property
    def wup_ip(self):
        if not hasattr(self, '_wup_ip') or self.IP_REGEX.match(self._wup_ip) is None:
            self.wup_ip = self.ask_wup_ip()
        return self._wup_ip

    @wup_ip.setter
    def wup_ip(self, value):
         if self.IP_REGEX.match(value):
            self._wup_ip = value

    @property
    def wup_client(self):
        if not hasattr(self, '_wup_client') or self._wup_client is None:
            self._wup_client = create_wupclient(self.wup_ip)
        return self._wup_client

    def close(self):
        if self._wup_client is not None:
            self._wup_client.kill()
            self._wup_client = None

    def ask_wup_ip(self):
        ip = ''
        while self.IP_REGEX.match(ip) is None:
            ip = input('Enter the IP address: ')
        print(f'IP address set: {ip}')
        return ip

    def guess_original_region(self, obj):
        '''
        JPN:
            code_id contains GJ || FJ
            model_number equals WUP-001(01) || WUP-101(01)
        EUR:
            code_id contains GE || GA (AUS) || FE
            model_number equals WUP-001(03) || WUP-001(04) || WUP-101(03) || WUP-101(04)
        USA:
            code_id contains GW || GB (BRA) || FW || FM || FU 
            model_number equals WUP-001(02) || WUP-001(14) || WUP-101(02) || WUP-901(02)
        Kiosk:
            WIS-001 || WUT-001 || WUT-002 || WUT-011
        Based on: https://wiiu.gerbilsoft.com/?sort=system_model
        '''
        if isinstance(obj, dict) and 'model_number' in obj or 'code_id' in obj or 'product_area' in obj:
            if 'model_number' in obj and (model := self.MODEL_NUMBER_REGEX.match(obj['model_number']['value'])) is not None:
                model = model.groupdict()
                if isinstance(model, dict) and 'region' in model and model['region']:
                    return self.guess_original_region(model['region'])
            if 'code_id' in obj and len(obj['code_id']['value'][0:2]) == 2:
                return self.guess_original_region(obj['code_id']['value'][1])
            #return self.guess_original_region(obj['product_area']['value'])
        if obj.upper() in ('1', '01', 'J', 'JPN'):
            return (1, 'JPN')
        if obj.upper() in ('2', '02', '12', '14', 'B', 'BRA', 'M', 'U', 'USA', 'W'):
            return (2, 'USA')
        if obj.upper() in ('4', '04', '3', '03', 'A', 'AUS', 'E', 'EUR'):
            return (4, 'EUR')

    def get_sys_prod_region_or_ask(self, msg='Enter the original region (1 = JPN, 2 = USA, 4 = EUR): '):
        if hasattr(self, '_sys_prod') and isinstance(self._sys_prod, dict) and (r := self.guess_original_region(self._sys_prod)) is not None:
            return r
        return self.ask_region(msg)

    def ask_region(self, msg='Enter the desired region (1 = JPN, 2 = USA, 4 = EUR): '):
        region = self.guess_original_region(input(msg))
        if not region:
            return self.ask_region()
        return region

    def flush_mlc(self):
        w = self.wup_client
        print('Flushing MLC')
        ret = w.FSA_FlushVolume(w.fsa_handle, w.cwd)
        print(hex(ret))

    def force_restart(self):
        w = self.wup_client
        print('Restarting Wii U')
        w.svc_and_exit(0x72, [1])
        self.close()

    def create_update_folder(self, flags=0x777):
        w = self.wup_client
        update_folder = f'{w.cwd}/sys/update'
        w.rmdir(update_folder)
        w.mkdir(update_folder, flags)
        self.flush_mlc()

    def _has_payload_loader_payload(self, obj):
        return re_findall(obj, r'">(.*)</default_title_id>') in (
            '000500101004E000',#Health and Safety Information JPN
            '000500101004E100',#Health and Safety Information USA
            '000500101004E200',#Health and Safety Information EUR
        )

    def set_system_setting_as_coldboot(self):
        #Check if there are any System Setting installed before setting this
        w = self.wup_client
        w.dl(self.COOLBOOT_PATH)
        if (sys_xml := read_file('system.xml')) is not None:
            region = self.get_sys_prod_region_or_ask()
            coldboot_title = {
                'JPN':'00050010-10047000',
                'USA':'00050010-10047100',
                'EUR':'00050010-10047200',
            }.get(region[1])
            if w.cd(f'{w.cwd}/sys/title/{coldboot_title.replace("-", "/")}') < 0 or self._has_payload_loader_payload(sys_xml) and not ask_and_boolify('WARNING: PAYLOAD LOADER IS STILL INSTALLED, ARE YOU SURE? (Y)es or (N)o'):
                return None
            sys_xml = re_sub(sys_xml, r'">(.*)</default_title_id>', f'">{coldboot_title.replace("-", "")}</default_title_id>')
            write_file(sys_xml, 'system_edited.xml')
            if os.path.exists('system_edited.xml'):
                w.up('system_edited.xml', self.COOLBOOT_PATH)

    def system_titles_remover(self, auto_flush=True):
        if not ask_and_boolify('WARNING: REMOVING SYSTEM TITLES CAN BRICK YOUR CONSOLE, ARE YOU SURE? (Y)es or (N)o'):
            return None
        region = self.get_sys_prod_region_or_ask()
        if (titles := SYSTEM_TITLES.get(region[1])) is not None and isinstance(titles, (tuple, list)):
            w = self.wup_client
            for t in titles:
                w.rmdir(f"{w.cwd}/sys/title/{t.replace('-', '/')}")
            if auto_flush:
                self.flush_mlc

    def wiiu_region_changer(self):
        w = self.wup_client
        w.dl(self.SYS_PROD_PATH)
        if (sys_prod := read_file('sys_prod.xml')) is not None:
            game_region = self.ask_region()
            self._sys_prod = extract_sys_prod(sys_prod)
            sys_prod = re_sub(sys_prod, r'">[^/]</product_area>', f'">{game_region[0]}</product_area>')
            sys_prod = re_sub(sys_prod, r'">[^/]</game_region>', f'">{self.REGION_HAX}</game_region>')
            write_file(sys_prod, 'sys_prod_edited.xml')
            if os.path.exists('sys_prod_edited.xml'):
                w.up('sys_prod_edited.xml', self.SYS_PROD_PATH)
                #self.create_update_folder()
                #self.force_restart()

    def gamepad_update_remover(self, version_check_flag=0):
        if not ask_and_boolify('WARNING: Are you sure you want to remove the Gamepad update? (Y)es or (N)o'):
            return None
        w = self.wup_client
        w.dl(self.DRC_CONFIG_PATH)
        if (drc_config := read_file('DRCCfg.xml')) is not None:
            drc_config = re_sub(drc_config, r'">[^/]</versionCheckFlag>', f'">{version_check_flag}</versionCheckFlag>')
            write_file(drc_config, 'DRCCfg_edited.xml')
            if os.path.exists('DRCCfg_edited.xml'):
                w.up('DRCCfg_edited.xml', self.DRC_CONFIG_PATH)

def inc_dec(b):
    if not b:
        return -1
    return 1

MENU = '''--------------- MENU ---------------
1. Wii U Region Changer
2. Gamepad Update Remover
3. Set System Setting as Cooldboot
4. (Re)create Update folder
5. Old System Titles Remover

9. Change WUP Server IP

0. Exit

> Input your choose: '''

#MENU = '''--------------- [TITLE] ---------------> Press Y for continue or B for back: '''

def main():
    '''Edit sys_prod, edit system.xml, create update, flush mlc and restart'''
    region_charger = RegionChanger()
    """
    menu_options = (
        {'title':'Wii U Region Changer', 'meth':region_charger.wiiu_region_changer}, #Change Region & Remove System Titles
        {'title':'Gamepad Update Remover', 'meth':region_charger.gamepad_update_remover}, #Remove Gamepad Update
        {'title':'Set System Setting as Cooldboot', 'meth':region_charger.set_system_setting_as_coldboot}, #Set System Setting as coldboot title
        {'title':'(Re)create Update folder', 'meth':region_charger.create_update_folder}, #(Re)create Update folder
        {'title':'Old System Titles Remover', 'meth':region_charger.system_titles_remover}, #Remove System titles
        {'title':'Change WUP Server IP', 'meth':region_charger.ask_wup_ip}, #Change WUP Server IP
    )
    choose = 0
    while 0 <= choose < len(menu_options):
        choose += inc_dec(ask_and_boolify(f"{choose} - {MENU.replace('TITLE', menu_options[choose]['title'])}"))
    """
    while (choose := input(MENU)) != '0':
        if choose == '1':
            #Change Region & Remove System Titles
            region_charger.wiiu_region_changer()
        elif choose == '2':
            #Remove Gamepad Update
            region_charger.gamepad_update_remover()
        elif choose == '3':
            #Set System Setting as coldboot title
            region_charger.set_system_setting_as_coldboot()
        elif choose == '4':
            #Remove System titles
            region_charger.create_update_folder()
        elif choose == '5':
            #Remove System titles
            region_charger.system_titles_remover()
        elif choose == '9':
            #Change WUP Server IP
            region_charger._wup_ip = region_charger.ask_wup_ip()

if __name__ == '__main__':
    main()
    #w = WupClient()
    #mount_sd()
    # mount_odd_content()

    # print(w.pwd())
    # w.ls()
    # w.dump_syslog()
    # w.mkdir('/vol/storage_sdcard/usr', 0x600)
    # install_title('test')
    # get_nim_status()
    # w.kill()
