"""Read-only LMTR v1 framing/index validator and deterministic reference writer.

The writer is a host-side corpus tool. It is not a live recorder, and makes no
queue/timer, SAF, fsync power-loss, or external close acknowledgement claim.
"""
import base64
from collections import OrderedDict
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import zlib
from .jsoncodec import ContractError, MAX63, dumps, loads, require
from .metadata import ROLES, SHAPES, Timeline, chunk, features, group, header, sha, utc

HEADER = struct.Struct('<8sHHIQQQ')
FOOTER = struct.Struct('<8sQQQ32sII')
MAGIC=b'LMTRREC1'; COMMIT=b'LMTRCMT1'
HARD_RECORD=65*1024*1024
DECODED_LIMIT=64*1024*1024
FAST_TAIL=68157512
JSON_LIMITS={1:1024*1024,3:512*1024,4:512*1024,5:512*1024}


# Create one promised envelope; body hash and header/footer CRC have separate domains.
def encode_record(kind, body, ordinal, previous):
    require(1<=kind<=5 and len(body)+112<=HARD_RECORD,'record bounds','resource_limit')
    h=HEADER.pack(MAGIC,1,kind,0,ordinal,len(body),previous)
    digest=hashlib.sha256(body).digest()
    f=FOOTER.pack(COMMIT,len(body)+112,ordinal,previous,digest,0,0)
    f=f[:64]+struct.pack('<II',zlib.crc32(h+f[:64]),0)
    return h+body+f


# Require one bounded RFC1950 stream, exact decoded length and no trailing bytes.
def inflate(stored, length):
    require(len(stored)>=6,'short zlib','invalid_payload')
    cmf,flg=stored[:2]
    require(cmf&15==8 and cmf>>4<=7 and (cmf*256+flg)%31==0 and not flg&32,'zlib header/dictionary','invalid_payload')
    decoder=zlib.decompressobj()
    try:
        result=decoder.decompress(stored,length+1)
        require(len(result)==length and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,'zlib length/ending/input','invalid_payload')
    except zlib.error as error:
        raise ContractError('committed_payload_integrity','zlib checksum/stream') from error
    return result


# Retain zero counters for every immutable declared stream, including unused slots.
def blank_summary(head):
    zero={r:'0' for r in ROLES}
    keys=['observations','temperature_planes','measurable_planes','gap_events','known_missing_observations','unknown_gap_events']
    return {'chunks':'0','entries':'0',**{k:'0' for k in keys},'physical_payload_bytes':'0','logical_bytes':zero.copy(),'streams':[{'stream_id':s['id'],**{k:'0' for k in keys},'logical_bytes':zero.copy()} for s in head['streams']], 'first_entry':None,'last_entry':None,'first_timeline_ns':None,'last_timeline_ns':None}


# Count only committed physical chunks and original logical payload roles.
def add_chunk(summary, metadata):
    # Counters remain decimal strings; integer addition never rounds through binary floats.
    def inc(target,key,amount=1): target[key]=str(int(target[key])+amount)
    inc(summary,'chunks'); inc(summary,'entries',len(metadata['entries']))
    inc(summary,'physical_payload_bytes',sum(int(b['decoded']) for b in metadata['blocks']))
    if summary['first_entry'] is None:
        summary['first_entry']=metadata['entries'][0]['entry_id']; summary['first_timeline_ns']=metadata['entries'][0]['timeline_ns']
    summary['last_entry']=metadata['entries'][-1]['entry_id']; summary['last_timeline_ns']=metadata['entries'][-1]['timeline_ns']
    streams={s['stream_id']:s for s in summary['streams']}
    for e in metadata['entries']:
        if e['kind'].startswith('segment_'): continue
        stream=streams[e['stream_id']]
        keys=[]
        if e['kind']=='gap':
            keys=[('gap_events',1),('unknown_gap_events',1)] if e['count'] is None else [('gap_events',1),('known_missing_observations',int(e['count']))]
        else:
            keys=[('observations',1)]
            if e['measurement']['status']=='available':
                keys.append(('temperature_planes',1))
                if e['measurement']['validity']!='no_valid_pixels': keys.append(('measurable_planes',1))
            for p in e['payloads']:
                role=p['role']; inc(stream['logical_bytes'],role,int(p['length'])); inc(summary['logical_bytes'],role,int(p['length']))
        for key,count in keys: inc(summary,key,count); inc(stream,key,count)


class Reader:
    """Seekable bounded reader; opening never claims unvisited payload integrity."""
    # Open and validate the source HEADER; failed construction always closes its descriptor.
    def __init__(self,path):
        self.path=Path(path); self.file=self.path.open('rb'); self.size=self.path.stat().st_size
        require(self.size<=MAX63,'file size','resource_limit')
        self.cancellation=lambda:False
        self.cache=OrderedDict(); self.cache_bytes=0; self.validated_records=set(); self.logical={}; self.selected=set()
        try:
            record=self.record(0)
            require(record['kind']==1 and record['ordinal']==0 and record['previous']==MAX63,'initial HEADER','invalid_container')
            self.head=record['json']; header(self.head); self.header_end=record['length']
        except BaseException:
            self.file.close()
            raise
    # Release only the owned read-only file descriptor.
    def close(self): self.file.close()
    # The reader is an owned read-only context; entry does not send device controls.
    def __enter__(self): return self
    # Close the source even when validation or cancellation raises.
    def __exit__(self,*unused): self.close()
    # Check file-relative bounds before a seek or allocation.
    def read(self,offset,length):
        require(0<=offset<=self.size and 0<=length<=self.size-offset,'read bounds','invalid_container')
        self.file.seek(offset); data=self.file.read(length)
        require(len(data)==length,'short read','invalid_container'); return data
    def envelope(self,offset):
        """Inspect fixed framing/CRC before trusting a predecessor or allocating a body."""
        h=self.read(offset,40); magic,revision,kind,flags,ordinal,length,previous=HEADER.unpack(h)
        require(magic==MAGIC,'record magic','unsupported_format' if offset==0 else 'invalid_container')
        require(revision==1,'envelope revision','unsupported_envelope')
        require(1<=kind<=5 and flags==0,'type/flags','invalid_container')
        require(max(ordinal,length,previous)<=MAX63,'binary integer bound','resource_limit')
        require(length+112<=HARD_RECORD,'record hard size','resource_limit')
        f=self.read(offset+40+length,72); marker,total,repeat,prev,digest,crc,reserved=FOOTER.unpack(f)
        require(marker==COMMIT and total==length+112 and repeat==ordinal and prev==previous and reserved==0,'footer repetitions','invalid_container')
        require(zlib.crc32(h+f[:64])==crc,'envelope CRC','invalid_container')
        if kind!=2: require(length<=JSON_LIMITS[kind],'metadata record size','resource_limit')
        return {'offset':offset,'kind':kind,'ordinal':ordinal,'previous':previous,'length':total,'digest':digest,'hash':sha(h+digest)}
    def record(self,offset, expected=None):
        """Verify a promised body and retain only bounded small navigation metadata."""
        require(not self.cancellation(), "cooperative reader cancellation", "cancelled")
        if offset in self.cache:
            record=self.cache.pop(offset); self.cache[offset]=record
        else:
            info=self.envelope(offset)
            kind,ordinal,previous,total,digest=[info[k] for k in ('kind','ordinal','previous','length','digest')]
            length=total-112
            body=self.read(offset+40,length)
            require(hashlib.sha256(body).digest()==digest,'committed body hash','committed_payload_integrity' if kind==2 else 'invalid_container')
            record={'offset':offset,'kind':kind,'ordinal':ordinal,'previous':previous,'length':total,'body':body,'hash':info['hash']}
            if kind!=2:
                record['json']=loads(body)
                if kind==1: header(record['json'])
                else:
                    # A well-typed but ninth navigation level exceeds the frozen
                    # hard resource limit; it is not an unfamiliar optional shape.
                    depth=record['json'].get('depth') if kind==3 else None
                    if type(depth) is int and 0<=depth<=2147483647:require(depth<=7,'index depth bound','resource_limit')
                    SHAPES.check({3:'IndexPage',4:'Checkpoint',5:'End'}[kind],record['json'])
            require(len(self.validated_records)<65536 or offset in self.validated_records,'reference-tool record reporting budget','local_resource_limit')
            self.validated_records.add(offset)
            # Only small navigation records are cached. At most 16 bounded pages.
            if kind in (3,4,5):
                self.cache[offset]=record; self.cache_bytes+=len(body)
                if kind==3:
                    same_depth=[key for key,item in self.cache.items() if item['kind']==3 and item['json']['depth']==record['json']['depth']]
                    while len(same_depth)>2:
                        evicted=self.cache.pop(same_depth.pop(0)); self.cache_bytes-=len(evicted['body'])
                while len(self.cache)>16 or self.cache_bytes>8*1024*1024:
                    _,old=self.cache.popitem(last=False); self.cache_bytes-=len(old['body'])
        if expected is not None: require(record['kind']==expected,'referenced record type','invalid_container')
        return record
    # Validate END/checkpoint closure without certifying unvisited image payloads.
    def terminal(self):
        f=self.read(self.size-72,72); marker,total,*_=FOOTER.unpack(f)
        require(marker==COMMIT and 112<=total<=HARD_RECORD and total<=self.size,'terminal footer','invalid_container')
        end=self.record(self.size-total,5)
        require(end['offset']+end['length']==self.size,'trailing bytes','invalid_container')
        data=end['json']; self.terminal_record=end
        require(data['recording_id']==self.head['recording_id'],'END identity')
        checkpoint=self.reference(data['checkpoint'],end,4)
        require(checkpoint['json']['recording_id']==self.head['recording_id'] and data['summary']==checkpoint['json']['summary'] and data['root']==checkpoint['json']['root'],'END checkpoint closure','invalid_container')
        require(data['status']=='failed' if data['error'] is not None else data['status']!='failed','END error closure')
        utc(data['finalization_time']); self.summary_shape(data['summary'])
        self.checkpoint_shape(checkpoint)
        return end
    # Read exact child bounds from the referenced record, rather than trusting its parent.
    def bounds(self,record):
        if record['kind']==2:
            value=self.metadata(record)
            return value['entries'][0]['entry_id'],value['entries'][-1]['entry_id']
        if record['kind']==3: return record['json']['first'],record['json']['last']
        if record['kind']==4: return record['json']['summary']['first_entry'],record['json']['summary']['last_entry']
        require(False,'reference bound type','invalid_container')
    # Verify backward location, ordinal, type, hash and actual entry bounds, including cache hits.
    def reference(self,ref,owner,expected):
        offset,length,ordinal=[int(ref[k]) for k in ('offset','length','ordinal')]
        require(0<=offset<=MAX63 and 0<=length<=MAX63-offset,'reference arithmetic','resource_limit')
        require(offset+length<=owner['offset'] and ordinal<owner['ordinal'],'forward reference','invalid_container')
        record=self.record(offset,expected)
        require(record['length']==length and record['ordinal']==ordinal and record['hash']==ref['hash'] and self.bounds(record)==(ref['first'],ref['last']),'reference fields/hash/range','invalid_container')
        return record
    # Validate bounded chunk closure and exact contiguous physical storage before decoding.
    def metadata(self,record):
        body=record['body']; require(len(body)>=4,'chunk length prefix','invalid_container')
        length=struct.unpack_from('<I',body)[0]
        require(length<=1024*1024 and length<=len(body)-4,'chunk metadata length','resource_limit')
        value=loads(body[4:4+length])
        for block in value.get('blocks',[]) if isinstance(value.get('blocks',[]),list) else []:
            if isinstance(block,dict) and type(block.get('codec')) is int: require(block['codec'] in (0,1),'block codec','unsupported_codec')
        SHAPES.check('Chunk',value); record['metadata']=value
        require(value['recording_id']==self.head['recording_id'],'chunk identity')
        features(value['required_features']); require(set(value['required_features'])<=set(self.head['required_features']),'feature declaration')
        require(sum(int(b['decoded']) for b in value['blocks'])+length+4<=DECODED_LIMIT,'decoded physical budget','resource_limit')
        cursor=0
        for b in value['blocks']:
            require(int(b['offset'])==cursor,'stored blocks contiguous','invalid_payload'); cursor+=int(b['stored'])
            require(b['codec'] in self.head['codecs'] and b['codec'] in (0,1),'codec negotiation','unsupported_codec')
        require(cursor==len(body)-4-length,'stored area exact coverage','invalid_payload')
        return value
    # Verify codec ending, decoded hash and logical payload authority before exposing data.
    def decode(self,record):
        value=self.metadata(record); prefix=4+struct.unpack_from('<I',record['body'])[0]; blocks={}
        for b in value['blocks']:
            start=prefix+int(b['offset']); stored=record['body'][start:start+int(b['stored'])]
            data=stored if b['codec']==0 else inflate(stored,int(b['decoded']))
            require(len(data)==int(b['decoded']),'STORED decoded length','invalid_payload')
            require(sha(data)==b['hash'],'decoded block hash','committed_payload_integrity'); blocks[b['id']]=data
        result=chunk(value,blocks,self.head)
        require(len(self.logical)+len(result['logical_hashes'])<=65536 or record['offset'] in self.selected,'logical-hash reporting budget','local_resource_limit')
        self.logical.update(result['logical_hashes']); self.selected.add(record['offset'])
        return value,result
    # Check local ordered/disjoint ranges and exact aggregate metadata for one page.
    def index_page(self,record):
        page=record['json']; children=page['children']; refs=[c['record'] for c in children]
        for child in children: require(child['stream_ids']==sorted(set(child['stream_ids'])),'index child sorted streams')
        require(all(r['first'] is not None and r['last'] is not None and int(r['first'])<=int(r['last']) for r in refs),'page ranges','invalid_container')
        require(all(int(a['last'])+1==int(b['first']) for a,b in zip(refs,refs[1:])),'page range continuity','invalid_container')
        require(page['first']==refs[0]['first'] and page['last']==refs[-1]['last'],'page first/last','invalid_container')
        require(page['time_first_ns']==children[0]['time_first_ns'] and page['time_last_ns']==children[-1]['time_last_ns'],'page time bounds','invalid_container')
        require(all(int(c['time_first_ns'])<=int(c['time_last_ns']) for c in children) and all(int(a['time_last_ns'])<=int(b['time_first_ns']) for a,b in zip(children,children[1:])),'page time order','invalid_container')
        require(page['stream_ids']==sorted({s for c in children for s in c['stream_ids']}),'page stream union','invalid_container')
        require(int(page['entries'])==int(page['last'])-int(page['first'])+1,'page entry count','invalid_container')
    # Traverse bounded-depth pages lazily; duplicate leaves cannot create synthetic measurements.
    def walk(self,ref,owner,entry=None,seen=None):
        seen=set() if seen is None else seen; page_record=self.reference(ref,owner,3); self.index_page(page_record); page=page_record['json']
        chunks=entries=0
        for child in page['children']:
            r=child['record']
            if entry is not None and not int(r['first'])<=entry<=int(r['last']): continue
            record=self.reference(r,page_record,2 if page['depth']==0 else 3)
            if page['depth']==0:
                require(record['offset'] not in seen,'duplicate reachable leaf','invalid_container'); seen.add(record['offset'])
                metadata,result=self.decode(record)
                streams=result['stream_ids']; first=metadata['entries'][0]['timeline_ns']; last=metadata['entries'][-1]['timeline_ns']; chunks+=1; entries+=len(metadata['entries'])
                yield record,metadata
            else:
                require(record['json']['depth']==page['depth']-1,'page depth','invalid_container')
                streams=record['json']['stream_ids']; first=record['json']['time_first_ns']; last=record['json']['time_last_ns']
                chunks+=int(record['json']['chunks']); entries+=int(record['json']['entries'])
                yield from self.walk(r,page_record,entry,seen)
            require(child['stream_ids']==streams and child['time_first_ns']==first and child['time_last_ns']==last,'index child metadata','invalid_container')
        if entry is None: require(int(page['chunks'])==chunks and int(page['entries'])==entries,'page child sums','invalid_container')
    # Require declared stream inventory and exact aggregate/empty-recording counters.
    def summary_shape(self,value):
        require([s['stream_id'] for s in value['streams']]==[s['id'] for s in self.head['streams']],'summary stream inventory')
        for key in ('observations','temperature_planes','measurable_planes','gap_events','known_missing_observations','unknown_gap_events'):
            require(int(value[key])==sum(int(s[key]) for s in value['streams']),'summary stream counts')
        for role in ROLES: require(int(value['logical_bytes'][role])==sum(int(s['logical_bytes'][role]) for s in value['streams']),'summary logical counts')
        if int(value['chunks'])==0: require(value==blank_summary(self.head),'empty summary')
        else: require(value['first_entry']=='0' and int(value['last_entry'])==int(value['entries'])-1 and int(value['first_timeline_ns'])<=int(value['last_timeline_ns']),'summary bounds')
    # Bind checkpoint roots and last chunks to actual verified immutable records.
    def checkpoint_shape(self,record):
        value=record['json']; self.summary_shape(value['summary']); nonempty=int(value['summary']['chunks'])>0
        require((value['root'] is not None)==nonempty and (value['last_chunk'] is not None)==nonempty,'checkpoint root/last closure')
        if nonempty:
            root=self.reference(value['root'],record,3); self.index_page(root)
            require(root['json']['first']==value['summary']['first_entry'] and root['json']['last']==value['summary']['last_entry'] and root['json']['entries']==value['summary']['entries'] and root['json']['chunks']==value['summary']['chunks'],'root summary','invalid_container')
            last=self.reference(value['last_chunk'],record,2); require(self.bounds(last)[1]==value['summary']['last_entry'],'last chunk bound','invalid_container')
        if value['previous_checkpoint'] is not None: self.reference(value['previous_checkpoint'],record,4)
    # Stream physical records and every historical snapshot before comparing the final index.
    def full(self,end):
        summary=blank_summary(self.head); timeline=Timeline(); offsets=set(); last_chunk=None; previous=MAX63; ordinal=0; cursor=0; pending=0; latest_checkpoint=None
        while cursor<self.size:
            record=self.record(cursor)
            require(record['ordinal']==ordinal and record['previous']==previous,'sequential predecessor/ordinal','invalid_container')
            require(record['kind']!=1 or cursor==0,'duplicate HEADER','invalid_container')
            if record['kind']==2:
                metadata,_=self.decode(record); timeline.accept(metadata); add_chunk(summary,metadata); offsets.add(cursor); last_chunk=record; pending+=1
                require(pending<=32,'checkpoint chunk cadence')
            elif record['kind']==3:
                # Validate orphan snapshot pages too; page targets are checked during tree traversal.
                self.index_page(record)
                for _ in self.walk({'offset':str(cursor),'length':str(record['length']),'ordinal':str(record['ordinal']),'first':record['json']['first'],'last':record['json']['last'],'hash':record['hash']}, {'offset':cursor+record['length'],'ordinal':ordinal+1}):
                    pass
            elif record['kind']==4:
                self.checkpoint_shape(record); value=record['json']; require(value['summary']==summary,'checkpoint actual summary')
                require(value['previous_checkpoint']==(make_ref(latest_checkpoint) if latest_checkpoint else None),'previous checkpoint chain')
                require(value['last_chunk']==(make_ref(last_chunk) if last_chunk else None),'last chunk exact')
                pending=0; latest_checkpoint=record
            elif record['kind']==5:
                require(cursor+record['length']==self.size and timeline.active is None and pending==0,'END segment/drain/order')
                require(record['json']['summary']==summary and record['json']['checkpoint']==make_ref(latest_checkpoint),'END full summary')
            previous=cursor; ordinal+=1; cursor+=record['length']
        if end['json']['root'] is not None:
            reached={r['offset'] for r,_ in self.walk(end['json']['root'],end)}
            require(reached==offsets,'root indexes every CHUNK exactly once','invalid_container')
        return summary
    # Keep opening, selected access, full integrity and prefix recovery as distinct scopes.
    def check(self,full=False,entry=None,recover=False,cancelled=lambda:False):
        """Poll before record access, never publish a successful partial full-check report.

One admitted synchronous hash/decompression is not interruptible. The caller owns
context/close, including on cancellation. Restore callbacks even on failed recovery.
"""
        previous=self.cancellation;self.cancellation=cancelled
        try: return self._check(full,entry,recover)
        finally: self.cancellation=previous

    def _check(self,full=False,entry=None,recover=False):
        try: end=self.terminal()
        except ContractError as error:
            if not recover or error.code=="cancelled": raise
            return self.recovery(full,error)
        if full:
            summary=self.full(end); scope='full'
        elif entry is not None:
            require(end['json']['root'] is not None and 0<=entry<int(end['json']['summary']['entries']),'selected entry range')
            for _ in self.walk(end['json']['root'],end,entry):
                pass
            summary=end['json']['summary']; scope='selected_chunks'
        else: summary=end['json']['summary']; scope='opened_records'
        return {'status':'valid','declared_end_status':end['json']['status'],'integrity_scope':scope,'summary':summary,'logical_hashes':self.logical,'validated_records':len(self.validated_records),'selected_chunks':len(self.selected)}
    def seek(self,timeline_ns,stream_id=None):
        """First recorded entry at/after intake time, filtered by explicit participation.

Original sensor/receipt clocks never replace the common chronology. Segment
start/end entries involve their declared streams. Coarse unions may visit multiple
candidate chunks; local1024visit admission bounds work and retained state.
"""
        require(type(timeline_ns) is int and 0<=timeline_ns<=MAX63,'query timeline')
        require(stream_id is None or stream_id in {s['id'] for s in self.head['streams']},'query stream')
        end=self.terminal();visits=[0]
        def find(ref,owner):
            visits[0]+=1;require(visits[0]<=1024,'seek candidate budget','local_resource_limit')
            record=self.reference(ref,owner,3);self.index_page(record);page=record['json']
            for child in page['children']:
                if int(child['time_last_ns'])<timeline_ns or stream_id is not None and stream_id not in child['stream_ids']:continue
                target=self.reference(child['record'],record,2 if page['depth']==0 else 3)
                if page['depth']:
                    lower=target['json']
                    require(lower['depth']==page['depth']-1 and child['stream_ids']==lower['stream_ids'] and child['time_first_ns']==lower['time_first_ns'] and child['time_last_ns']==lower['time_last_ns'],'seek child navigation','invalid_container')
                    chosen=find(child['record'],record)
                    if chosen is not None:return chosen
                else:
                    visits[0]+=1;require(visits[0]<=1024,'seek candidate budget','local_resource_limit')
                    metadata,result=self.decode(target)
                    require(child['stream_ids']==result['stream_ids'] and child['time_first_ns']==metadata['entries'][0]['timeline_ns'] and child['time_last_ns']==metadata['entries'][-1]['timeline_ns'],'seek child metadata','invalid_container')
                    segments={s['id']:s for s in metadata['segments']}
                    for entry in metadata['entries']:
                        participates=stream_id is None or entry.get('stream_id')==stream_id or entry['kind'] in ('segment_start','segment_end') and stream_id in {s['stream_id'] for s in segments[entry['segment_id']]['streams']}
                        if int(entry['timeline_ns'])>=timeline_ns and participates:return entry
            return None
        chosen=find(end['json']['root'],end) if end['json']['root'] else None
        return {'status':'valid','integrity_scope':'selected_chunks' if self.selected else 'opened_records','declared_end_status':end['json']['status'],'summary':end['json']['summary'],'logical_hashes':self.logical,'validated_records':len(self.validated_records),'selected_chunks':len(self.selected),'seek_result':'found' if chosen else 'not_found','selected_entry_id':chosen['entry_id'] if chosen else None,'selected_timeline_ns':chosen['timeline_ns'] if chosen else None,'selected_entry_kind':chosen['kind'] if chosen else None,'requested_stream_id':stream_id}

    def salvage(self,cancelled=lambda:False):
        """Explicit cancellable scan of independently verified CHUNK ranges.

This never certifies global chronology, final index or source completeness. Bad
committed source bodies remain listed as integrity damage; no missing observation
is synthesized. Broken framing stops traversal without guessing a next offset.
"""
        cursor=0; ordinal=0; previous=MAX63; ranges=[]; failures=[]; cancelled_now=False
        while cursor<self.size:
            if cancelled(): cancelled_now=True;break
            require(len(ranges)+len(failures)<65536,'salvage reporting budget','local_resource_limit')
            try:
                envelope=self.envelope(cursor)
                require(envelope['ordinal']==ordinal and envelope['previous']==previous,'scan physical chain','invalid_container')
            except ContractError as error:
                failures.append({'offset':str(cursor),'error':error.code,'kind':'unknown_framing'});break
            try:
                record=self.record(cursor)
                if record['kind']==2:
                    metadata,_=self.decode(record)
                    ranges.append({'offset':str(cursor),'first_entry':metadata['entries'][0]['entry_id'],'last_entry':metadata['entries'][-1]['entry_id'],'first_timeline_ns':metadata['entries'][0]['timeline_ns'],'last_timeline_ns':metadata['entries'][-1]['timeline_ns']})
            except ContractError as error:
                if error.code in ('unsupported_required_feature','unsupported_codec','local_resource_limit','resource_limit'): raise
                failures.append({'offset':str(cursor),'error':error.code,'kind':'chunk' if envelope['kind']==2 else 'navigation'})
            previous=cursor; ordinal+=1;cursor+=envelope['length']
        return {'status':'integrity_damaged' if failures else 'recovered','integrity_scope':'salvaged_chunks','unknown_tail':True,'declared_end_status':None,'reason':'explicit_scan_cancelled' if cancelled_now else 'explicit_scan_no_global_certification','verified_prefix_bytes':str(cursor),'ranges':ranges,'failures':failures,'logical_hashes':self.logical,'cancelled':cancelled_now,'accuracy_warning':'Native-equivalent temperatures; absolute physical accuracy not yet independently validated.'}

    def recovery(self,full,cause):
        """Bounded verified-envelope walk; navigation damage never erases source corruption.

Only fixed framing authenticates predecessor links. Rebuildable metadata may be
skipped; every complete source CHUNK remains promised and must validate. A full
prefix scope requires independent indexed prefix and suffix journal verification.
"""
        lower=max(0,self.size-FAST_TAIL); end=self.size; candidates=0; anchor=None
        while end>lower and anchor is None:
            start=max(lower,end-65536); window=self.read(start,end-start); at=len(window)
            while True:
                position=window.rfind(COMMIT,0,at)
                if position<0: break
                at=position; candidates+=1
                require(candidates<=1024,'candidate recovery bound','recovery_limit')
                footer=start+position
                if self.size-footer<72: continue
                total=FOOTER.unpack(self.read(footer,72))[1]
                if not 112<=total<=HARD_RECORD or total>footer+72: continue
                try:
                    possible=self.envelope(footer+72-total)
                    if possible['offset']+possible['length']==footer+72: anchor=possible;break
                except ContractError: pass
            if start==lower: break
            end=start+7
        if anchor is None:
            require(self.size-self.header_end<=FAST_TAIL,'tail has no verifiable anchor','recovery_limit')
            anchor=self.envelope(0)
        suffix=[]; current=anchor; base=None; timeline=None; boundary=self.header_end; navigation_damage=False
        for _ in range(1024):
            try: record=self.record(current['offset'])
            except ContractError as error:
                if error.code=="cancelled" or current['kind'] in (1,2): raise
                record=None; navigation_damage=True
            if record is not None and record['kind']==2:
                require(len(suffix)<64,'suffix CHUNK bound','recovery_limit')
                self.decode(record); suffix.append(current)
            if record is not None and record['kind']==4:
                try:
                    self.checkpoint_shape(record)
                    candidate_timeline=Timeline(); recalculated=blank_summary(self.head)
                    for _,metadata in self.walk(record['json']['root'],record) if record['json']['root'] else []:
                        if full: candidate_timeline.accept(metadata);add_chunk(recalculated,metadata)
                    if full: require(recalculated==record['json']['summary'],'recovered checkpoint summary')
                    base=deepcopy(record['json']['summary']);timeline=candidate_timeline if full else None
                    boundary=record['offset']+record['length'];break
                except ContractError as error:
                    if error.code in ('cancelled','committed_payload_integrity','unsupported_required_feature','unsupported_codec','local_resource_limit'): raise
                    navigation_damage=True
            if current['offset']==0:
                timeline=Timeline();break
            previous=self.envelope(current['previous'])
            require(previous['offset']+previous['length']==current['offset'] and previous['ordinal']+1==current['ordinal'],'recovery predecessor','invalid_container')
            current=previous
        else: require(False,'predecessor recovery bound','recovery_limit')
        summary=base if base is not None else blank_summary(self.head)
        for envelope in reversed(suffix):
            # Suffix state is fixed framing only, never up to64 retained large bodies.
            record=self.record(envelope['offset'])
            metadata,_=self.decode(record)
            require(int(metadata['entries'][0]['entry_id'])==int(summary['entries']) and (summary['last_timeline_ns'] is None or int(metadata['entries'][0]['timeline_ns'])>=int(summary['last_timeline_ns'])),'recovered suffix order/time')
            if timeline: timeline.accept(metadata)
            add_chunk(summary,metadata);boundary=max(boundary,record['offset']+record['length'])
        return {'status':'recovered','verified_prefix_bytes':str(boundary),'last_verified_entry':summary['last_entry'],'last_verified_timeline_ns':summary['last_timeline_ns'],'declared_end_status':None,'unknown_tail':True,'integrity_scope':'full_verified_prefix' if full else 'selected_chunks','reason':'navigation_rebuilt' if navigation_damage else 'missing_or_torn_end','opening_error':cause.code,'summary':summary,'logical_hashes':self.logical,'validated_records':len(self.validated_records),'selected_chunks':len(self.selected)}


# Bind navigation to immutable envelope identity and actual metadata bounds.
def make_ref(record,first=None,last=None):
    if record is None: return None
    if first is None:
        if record['kind']==2: first,last=record['metadata']['entries'][0]['entry_id'],record['metadata']['entries'][-1]['entry_id']
        elif record['kind']==3: first,last=record['json']['first'],record['json']['last']
        elif record['kind']==4: first,last=record['json']['summary']['first_entry'],record['json']['summary']['last_entry']
    return {'offset':str(record['offset']),'length':str(record['length']),'ordinal':str(record['ordinal']),'first':first,'last':last,'hash':record['hash']}


def recipe_bytes(recipe,depth=0):
    """Bounded non-wire materialization; verify requested allocation before creating bytes."""
    require(isinstance(recipe,dict),'recipe object')
    require(depth<32,'recipe nesting','resource_limit')
    # Requested byte products are admitted before allocating their repeated scalar.
    def count(width):
        require(type(recipe['count']) is int and recipe['count']>0,'recipe integer count')
        require(recipe['count']<=DECODED_LIMIT//width,'recipe allocation budget','resource_limit')
        return recipe['count']
    # NaN/Inf evidence is explicit hex transport, never a synthetic scalar coercion.
    def finite(value):
        require(type(value) is not bool,'recipe numeric scalar')
        number=float(value);require(math.isfinite(number),'finite recipe scalar');return number
    kind=recipe['kind']
    if kind=='hex':
        text=recipe['hex'];require(isinstance(text,str),'hex recipe');require(len(text)<=2*DECODED_LIMIT,'hex allocation','resource_limit')
        return bytes.fromhex(text)
    if kind=='f32_values':
        values=recipe['values'];require(isinstance(values,list),'recipe values')
        require(len(values)<=DECODED_LIMIT//4,'recipe value allocation','resource_limit')
        return b''.join(struct.pack('<f',finite(x)) for x in values)
    if kind=='f32_constant':return struct.pack('<f',finite(recipe['value']))*count(4)
    if kind=='u16_constant':
        value=recipe['value'];require(type(value) is int and 0<=value<=65535,'uint16 recipe scalar')
        return struct.pack('<H',value)*count(2)
    if kind=='transport':
        native=recipe_bytes(recipe['native'],depth+1);tail=recipe['trailer_bytes']
        require(type(tail) is int and tail>=0,'trailer integer count')
        require(tail<=DECODED_LIMIT-len(native),'trailer allocation','resource_limit')
        return native+bytes(tail)
    require(False,'unsupported recipe kind')


# Materialize owned synthetic recipes and independently verify native views before publication.
def prepare_chunk(item,head):
    metadata=deepcopy(item['metadata']); incoming={b['id']:b for b in item.get('blocks',[])}
    for block in metadata.get('blocks',[]):
        if type(block.get('codec')) is int:require(block['codec'] in (0,1),'writer codec','unsupported_codec')
    SHAPES.check('Chunk',metadata)
    require(4+len(dumps(metadata))+sum(int(b['decoded']) for b in metadata['blocks'])<=DECODED_LIMIT,'plan physical allocation budget','resource_limit')
    byblock={b['id']:b for b in metadata['blocks']}
    for entry in metadata['entries']:
        for payload in entry.get('payloads',[]):
            if payload['kind']=='materialized':
                require(payload['block_id'] in byblock,'plan block closure')
                require(int(payload['offset'])+int(payload['length'])<=int(byblock[payload['block_id']]['decoded']),'plan interval allocation','invalid_payload')
    if 'payload_recipes' in item:
        physical={b['id']:bytearray() for b in metadata['blocks']}
        for entry in metadata['entries']:
            for p in sorted(entry.get('payloads',[]),key=lambda p:p['id']):
                if p['kind']=='materialized':
                    data=recipe_bytes(item['payload_recipes'][entry['entry_id']+':'+p['id']]); require(len(data)==int(p['length']),'recipe descriptor length')
                    p['offset']=str(len(physical[p['block_id']])); physical[p['block_id']].extend(data); p['hash']=sha(data)
        incoming={b['id']:{'bytes':bytes(physical[b['id']]),'codec':b['codec']} for b in metadata['blocks']}
    stored=[]; decoded={}; cursor=0
    for b in metadata['blocks']:
        source=incoming[b['id']]; data=source.get('bytes')
        if data is None: data=base64.b64decode(source['bytes_base64'],validate=True)
        codec=source.get('codec',b['codec']); require(codec in (0,1),'writer codec','unsupported_codec')
        encoded=data if codec==0 else zlib.compress(data,1)
        b.update(offset=str(cursor),stored=str(len(encoded)),decoded=str(len(data)),codec=codec,hash=sha(data)); cursor+=len(encoded); stored.append(encoded); decoded[b['id']]=data
    # Descriptor logical hashes are generated from original independent recipe or exact materialized input.
    for e in metadata['entries']:
        byid={p['id']:p for p in e.get('payloads',[])}
        for p in byid.values():
            if p['kind']=='materialized': data=decoded[p['block_id']][int(p['offset']):int(p['offset'])+int(p['length'])]
            else:
                parent=byid[p['parent_payload_id']]; data=decoded[parent['block_id']][int(parent['offset'])+int(p['offset']):int(parent['offset'])+int(p['offset'])+int(p['length'])]
                if 'payload_recipes' in item:
                    require(data==recipe_bytes(item['payload_recipes'][e['entry_id']+':'+p['id']]),'view independent owned evidence mismatch')
            p['hash']=sha(data)
    result=chunk(metadata,decoded,head); encoded_json=dumps(metadata)
    require(len(encoded_json)<=1024*1024 and 4+len(encoded_json)+sum(len(b) for b in decoded.values())<=DECODED_LIMIT,'writer chunk budget','resource_limit')
    return metadata,struct.pack('<I',len(encoded_json))+encoded_json+b''.join(stored),result


def write_plan(plan,path):
    """Write a new private reference file, with no overwrite or input modification.

Plans are non-wire documents, so base64 recipes have a separate bounded policy.
All actual record JSON is separately subjected to the accepted wire constraints.
"""
    head=plan['header']; header(head); path=Path(path); timeline=Timeline(); summary=blank_summary(head)
    record_count=0; last_record=None; pending=0; levels=[[] for _ in range(8)]; last_chunk=None; previous_checkpoint=None
    with path.open('xb') as output:
        def append(kind,body,metadata=None):
            nonlocal record_count, last_record
            offset=output.tell(); previous=last_record['offset'] if last_record else MAX63; ordinal=record_count
            raw=encode_record(kind,body,ordinal,previous)
            # A footer is a promised commit only after the body barrier; acknowledge
            # this append only after a separate footer barrier also succeeds.
            output.write(raw[:-72]);output.flush();os.fsync(output.fileno())
            output.write(raw[-72:]);output.flush();os.fsync(output.fileno())
            record={'offset':offset,'length':len(raw),'ordinal':ordinal,'previous':previous,'kind':kind,'hash':sha(raw[:40]+hashlib.sha256(body).digest())}
            if metadata is not None: record['metadata' if kind==2 else 'json']=metadata
            record_count+=1; last_record=record; return record
        append(1,dumps(head),head)
        def page_value(children,depth):
            return {'depth':depth,'children':[{k:v for k,v in c.items() if not k.startswith('_')} for c in children], 'first':children[0]['record']['first'],'last':children[-1]['record']['last'],'time_first_ns':children[0]['time_first_ns'],'time_last_ns':children[-1]['time_last_ns'],'stream_ids':sorted({s for c in children for s in c['stream_ids']}),'chunks':str(sum(c['_chunks'] for c in children)),'entries':str(sum(c['_entries'] for c in children))}
        def fits_page(children,depth):
            return len(children)<=256 and len(dumps(page_value(children,depth)))<=512*1024
        def page_for(children,depth):
            page=page_value(children,depth)
            body=dumps(page); require(len(children)<=256 and len(body)<=512*1024,'index page size/fanout','resource_limit'); SHAPES.check('IndexPage',page)
            record=append(3,body,page)
            return {'record':make_ref(record),'time_first_ns':page['time_first_ns'],'time_last_ns':page['time_last_ns'],'stream_ids':page['stream_ids'],'_chunks':int(page['chunks']),'_entries':int(page['entries'])}
        def add_index(child,depth=0):
            require(depth<=7,'index depth exhaustion','resource_limit')
            proposal=levels[depth]+[child]
            if levels[depth] and not fits_page(proposal,depth):
                page=page_for(levels[depth],depth); levels[depth]=[]; add_index(page,depth+1)
            require(fits_page([child],depth),'singleton index page size','resource_limit')
            levels[depth].append(child)
            if len(levels[depth])==256:
                page=page_for(levels[depth],depth); levels[depth]=[]; add_index(page,depth+1)
        def snapshot():
            if not any(levels): return None
            # Snapshot only the bounded pending paths. Never insert these temporary
            # leaves into live state: a later checkpoint would otherwise duplicate them.
            carry=[]
            for depth in range(8):
                children=levels[depth]+carry
                if not children: continue
                batches=[]; batch=[]
                for child in children:
                    if batch and not fits_page(batch+[child],depth): batches.append(batch); batch=[]
                    require(fits_page([child],depth),'singleton snapshot page','resource_limit'); batch.append(child)
                if batch: batches.append(batch)
                carry=[page_for(batch,depth) for batch in batches]
                if not any(levels[depth+1:]) and len(carry)==1: return carry[0]['record']
            require(False,'snapshot depth exhaustion','resource_limit')
        def checkpoint():
            nonlocal previous_checkpoint
            root=snapshot(); value={'recording_id':head['recording_id'],'root':root,'last_chunk':make_ref(last_chunk),'previous_checkpoint':make_ref(previous_checkpoint),'summary':deepcopy(summary)}
            previous_checkpoint=append(4,dumps(value),value); return root
        for item in plan['chunks']:
            metadata,body,result=prepare_chunk(item,head); timeline.accept(metadata); record=append(2,body,metadata); add_chunk(summary,metadata)
            last_chunk=record
            add_index({'record':make_ref(record),'time_first_ns':metadata['entries'][0]['timeline_ns'],'time_last_ns':metadata['entries'][-1]['timeline_ns'],'stream_ids':result['stream_ids'],'_chunks':1,'_entries':len(metadata['entries'])})
            pending+=1
            if pending==32: checkpoint(); pending=0
        require(timeline.active is None,'finalization with open segment')
        root=checkpoint(); status=plan.get('status','completed')
        end={'recording_id':head['recording_id'],'root':root,'checkpoint':make_ref(previous_checkpoint),'summary':deepcopy(summary),'status':status,'reason':plan.get('reason','normal_stop'),'finalization_time':plan.get('finalization_time',{'status':'unknown','clock_source':'host_utc','reason':'not_recorded'}),'error':plan.get('error')}
        SHAPES.check('End',end); append(5,dumps(end),end)
    with Reader(path) as reader: return reader.check(full=True)
