"""Independent LMTR metadata, payload and chronology invariants.

Byte data remain authoritative. Cached JSON readings are checked against exact
Float32 bytes and never used to synthesize a plane.
"""
from decimal import Decimal
import hashlib
import math
import struct
from .jsoncodec import MAX63, Shapes, dumps, require

ROLES = ['temperature', 'validity', 'native_samples', 'acquisition', 'preview', 'visible', 'module_extension']
DTYPES = ['u8', 'i8', 'u16', 'i16', 'u32', 'i32', 'u64', 'i64', 'f32', 'f64', 'bytes']
ORDERS = ['little', 'big', 'not_applicable']
WIDTH = {'u8':1,'i8':1,'u16':2,'i16':2,'u32':4,'i32':4,'u64':8,'i64':8,'f32':4,'f64':8}
CORE = 'org.lmthermal.lmtr.core-v1'
VIEW = 'org.lmthermal.lmtr.contiguous-native-view-v1'
FEATURES = {CORE, VIEW}
SHAPES = Shapes()

# Hash exact supplied bytes without semantic JSON reserialization.
def sha(data): return hashlib.sha256(data).hexdigest()
# Convert a previously shape-validated decimal-string counter exactly.
def number(value): return int(value)
# Order physical blocks by the accepted role/encoding/dtype/endian tuple.
def group(block): return (ROLES.index(block['role']), block['encoding_id'], DTYPES.index(block['dtype']), ORDERS.index(block['byte_order']))
# Refuse unsorted or duplicate source data instead of silently normalizing its meaning.
def sorted_unique(items, key=lambda x:x): require([key(x) for x in items] == sorted(set(key(x) for x in items)), 'ordered unique list')

# Reject missing core or unsupported mandatory interpretations before accepting content.
def features(items):
    sorted_unique(items)
    require(CORE in items, 'missing core feature')
    require(set(items) <= FEATURES, 'required feature not supported', 'unsupported_required_feature')

# Keep known versus unavailable time evidence explicit; no fabricated UTC fallback.
def utc(value):
    status = value['status']
    require(('utc' in value) if status == 'known' else ('reason' in value), 'UTC evidence closure')
    require('reason' not in value if status == 'known' else True, 'known UTC has reason')
    require('utc' not in value if status == 'unknown' else True, 'unknown UTC has reading')

# Enforce known/partial/unavailable evidence unions and their required explanations.
def evidence(value):
    status = value['status']
    require(('value' in value) == (status in ('known', 'partial')), 'evidence value closure')
    require(('reason' in value) == (status != 'known'), 'evidence reason closure')

# Negotiate only declared extension wrappers; optional nested JSON remains opaque data.
def extensions(value, declared):
    for key, extension in value.get('extensions', {}).items():
        required = extension['required_feature']
        require(required is None or required in declared, 'extension feature undeclared')

# Validate native dimensions and bounded pixel products without presentation transforms.
def geometry(value):
    if value is not None:
        require(value['width_px'] * value['height_px'] <= 4194304, 'geometry pixels', 'resource_limit')

# Validate identity, capabilities, immutable inventory and supported wire interpretation.
def header(value):
    require(isinstance(value,dict),'HEADER object')
    if isinstance(value.get('format'),str): require(value['format']=='lmthermal-recording','format','unsupported_format')
    for version in (value.get('container_version',{}),value.get('schema_version',{})):
        if isinstance(version,dict) and type(version.get('major')) is int: require(version['major']==1,'major version','unsupported_major')
    for codec in value.get('codecs',[]) if isinstance(value.get('codecs',[]),list) else []:
        if type(codec) is int: require(codec in (0,1),'header codec','unsupported_codec')
    SHAPES.check('Header', value)
    require(value['format'] == 'lmthermal-recording', 'format', 'unsupported_format')
    for version in (value['container_version'], value['schema_version']):
        require(version['major'] == 1, 'major version', 'unsupported_major')
    features(value['required_features'])
    sorted_unique(value['codecs'])
    require(set(value['codecs']) <= {0,1}, 'header codec', 'unsupported_codec')
    sorted_unique(value['streams'], lambda x:x['id'])
    sorted_unique(value['privacy']['consented_namespaces'])
    utc(value['creation_time'])
    extensions(value, value['required_features'])
    if 'lineage' in value:
        evidence(value['lineage']['algorithm'])
        # Approach B cannot bind the historical directory/NPZ dataset as one Parent file.
        for parent in value['lineage']['parents']:
            require(parent['format_id']!='lmthermal-radiometric-recording','legacy dataset conversion requires future reviewed semantics','unsupported_required_feature')
            require(parent['recording_id']!=value['recording_id'],'self parent')
        if value['lineage']['operation'] == 'reprocessing':
            require(value['lineage']['algorithm']['status'] == 'known', 'reprocessing algorithm')

# Compute deterministic row-major finite-valid results from original Float32 bytes.
def statistics(data, mask, width, bounds=None):
    require(type(width) is int and 0<width<=16384 and len(data)%4==0,'matrix dimensions','invalid_payload')
    pixels=len(data)//4
    require(0<pixels<=4194304 and pixels%width==0 and pixels//width<=16384,'matrix product','resource_limit')
    require(mask is None or len(mask)==pixels and all(bit in (0,1) for bit in mask),'statistics validity mask','invalid_payload')
    rectangle=bounds if bounds is not None else (0,0,width,pixels//width)
    require(len(rectangle)==4,'ROI dimensions','invalid_payload')
    x1,y1,x2,y2=rectangle
    require(0<=x1<x2<=width and 0<=y1<y2<=pixels//width,'ROI bounds','invalid_payload')
    # Avoid image-sized Python float/tuple lists: one row-major accumulator keeps
    # a valid large grid from silently multiplying its allocation cost.
    count=0;total=0.0;low=math.inf;high=-math.inf;low_index=high_index=0
    for y in range(y1,y2):
        for x in range(x1,x2):
            index=y*width+x
            if mask is not None and mask[index]==0: continue
            value=struct.unpack_from('<f',data,index*4)[0]
            require(math.isfinite(value),'valid finite temperature','invalid_payload')
            if value<low:low=value;low_index=index
            if value>high:high=value;high_index=index
            total+=value;count+=1
    result={'pixel_count':(x2-x1)*(y2-y1),'valid_pixel_count':count}
    if count:result.update(min=low,max=high,mean=total/count,min_xy=[low_index%width,low_index//width],max_xy=[high_index%width,high_index//width])
    return result

# Compare advisory cached statistics with the authoritative source matrix.
def check_statistics(cache, actual):
    for field in ('pixel_count','valid_pixel_count','min_xy','max_xy'):
        require(cache.get(field) == actual.get(field), 'statistics ' + field, 'invalid_payload')
    for field in ('min','max','mean'):
        require((field in cache) == (field in actual), 'statistics valid fields', 'invalid_payload')
        if field in actual:
            require(math.isfinite(float(cache[field])) and abs(float(cache[field])-actual[field]) <= max(1e-4,1e-6*abs(actual[field])), 'statistics ' + field, 'invalid_payload')

def chunk(value, blocks, head):
    """Check one independent closure; return logical hashes and recomputed counters."""
    for block in value.get('blocks',[]) if isinstance(value.get('blocks',[]),list) else []:
        if isinstance(block,dict) and type(block.get('codec')) is int: require(block['codec'] in (0,1),'block codec','unsupported_codec')
    SHAPES.check('Chunk', value)
    require(value['recording_id'] == head['recording_id'], 'recording identity')
    features(value['required_features'])
    require(set(value['required_features']) <= set(head['required_features']), 'chunk/header features')
    for name in ('segments','contexts'):
        sorted_unique(value[name], lambda x:x['id'])
    segments = {s['id']:s for s in value['segments']}; contexts = {c['id']:c for c in value['contexts']}
    for segment in segments.values():
        require(segment['id'] == sha(dumps({k:v for k,v in segment.items() if k != 'id'})), 'segment content hash')
        sorted_unique(segment['streams'], lambda s:s['stream_id'])
        for stream in segment['streams']:
            require(stream['stream_id'] in {s['id'] for s in head['streams']}, 'undeclared stream')
            geometry(stream['geometry'])
            sorted_unique(stream['policy']['requested_roles'])
            sampling = stream['sampling']
            require((sampling['period_ns'] is None and sampling['origin_timeline_ns'] is None) if sampling['mode'] == 'acquisition' else (sampling['period_ns'] is not None and sampling['origin_timeline_ns'] is not None), 'sampling closure')
            require(len({c['id'] for c in stream['clocks']}) == len(stream['clocks']), 'clock IDs')
            for clock in stream['clocks']:
                require(clock['quality'] != 'known' or clock['tick_period_ns'] is not None, 'known clock period')
            for anchor in stream['utc_anchors']:
                require(anchor['stamp']['clock_domain_id'] in {c['id'] for c in stream['clocks']}, 'anchor clock')
                utc(anchor['utc'])
            extensions(stream, value['required_features'])
        extensions(segment, value['required_features'])
    for context in contexts.values():
        require(context['id'] == sha(dumps(context['value'])), 'context content hash')
        for name in ('algorithm','calibration','settings','environment'): evidence(context['value'][name])
        extensions(context['value'], value['required_features'])
    require(len(blocks) == len(value['blocks']), 'block count')
    require([group(b) for b in value['blocks']] == sorted(set(group(b) for b in value['blocks'])), 'block grouping', 'invalid_payload')
    slices = {b['id']:[] for b in value['blocks']}; logical = {}; used_contexts = set(); used_segments=set(); stream_ids=set()
    physical = sum(len(data) for data in blocks.values())
    for i,b in enumerate(value['blocks']):
        require(b['id'] == 'b'+str(i) and len(blocks[b['id']]) == int(b['decoded']), 'block ID/decoded size', 'invalid_payload')
    previous_entry=None
    for entry in value['entries']:
        if previous_entry is not None:
            require(int(entry['entry_id'])==int(previous_entry['entry_id'])+1 and int(entry['timeline_ns'])>=int(previous_entry['timeline_ns']),'local entry order/time')
        previous_entry=entry
        used_segments.add(entry['segment_id'])
        require(entry['segment_id'] in segments, 'entry segment closure')
        segment = segments[entry['segment_id']]
        require(int(entry['entry_id'])>=int(segment['start_entry']) and int(entry['timeline_ns'])>=int(segment['start_timeline_ns']),'entry before segment')
        if entry['kind']=='segment_start': require(entry['entry_id']==segment['start_entry'] and entry['timeline_ns']==segment['start_timeline_ns'],'segment start witness')
        streams = {s['stream_id']:s for s in segment['streams']}
        stream_ids.update(streams if entry['kind'].startswith('segment_') else [entry['stream_id']])
        extensions(entry, value['required_features'])
        if entry['kind'] in ('segment_start','segment_end'): continue
        require(entry['stream_id'] in streams, 'entry stream closure')
        stream=streams[entry['stream_id']]; clocks={c['id']:c for c in stream['clocks']}
        scheduled=entry['scheduled']
        if scheduled is not None:
            require(stream['sampling']['mode']=='periodic','schedule in acquisition')
            for a,b in (('first_slot','last_slot'),('first_deadline_ns','last_deadline_ns')): require(int(scheduled[a])<=int(scheduled[b]),'schedule range')
            for prefix in ('first','last'):
                require(int(scheduled[prefix+'_deadline_ns'])==int(stream['sampling']['origin_timeline_ns'])+int(scheduled[prefix+'_slot'])*int(stream['sampling']['period_ns']), 'schedule formula')
        if entry['kind']=='gap':
            counts=[]
            for prefix in ('device','observation'):
                first,last=entry[prefix+'_first'],entry[prefix+'_last']
                require((first is None)==(last is None),'gap range pair')
                if first is not None:
                    require(int(first)<=int(last),'gap range order'); counts.append(int(last)-int(first)+1)
            if scheduled: counts.append(int(scheduled['last_slot'])-int(scheduled['first_slot'])+1)
            require(all(c==int(entry['count']) for c in counts) if entry['count'] is not None else not counts,'gap count agreement')
            require(entry['count'] is None or entry['evidence']['basis']!='unknown','known gap lacks evidence')
            for side in ('before','after'):
                stamp=entry['evidence'][side]
                require(stamp is None or stamp['clock_domain_id'] in clocks,'gap clock closure')
            continue
        require(entry['context_id'] in contexts,'context closure'); used_contexts.add(entry['context_id'])
        require(contexts[entry['context_id']]['value']['source']==stream['source'],'context/source mismatch')
        for name in ('receipt','acquisition'):
            time=entry[name]; utc(time['utc']); stamp=time['stamp']; state=time['status']
            require((stamp is not None or time['utc']['status']=='known') if state=='known' else True,'known time has no reading')
            require(('reason' in time)==(state!='known'),'time reason')
            if state=='unreliable': require(stamp is not None or 'utc' in time['utc'],'unreliable time has no reading')
            if state in ('unknown','unsupported','unavailable'): require(stamp is None and time['utc']['status']=='unknown','absent time closure')
            if stamp is not None:
                require(stamp['clock_domain_id'] in clocks,'stamp domain')
                clock=clocks[stamp['clock_domain_id']]
                require(name!='receipt' or clock['kind']=='host_monotonic','receipt nonhost')
                require(state!='known' or clock['quality']=='known','known unreliable clock')
        if scheduled: require(scheduled['first_slot']==scheduled['last_slot'],'observation many scheduled slots')
        lineage=head.get('lineage')
        require(('parent_entry' in entry)==(bool(lineage) and lineage['operation'] in ('conversion','reprocessing')),'parent entry closure')
        if 'parent_entry' in entry:
            require(entry['parent_entry']['parent_index']<len(lineage['parents']),'parent index')
            if entry['parent_entry']['entry_id'] is None:
                require(bool(entry.get('extensions')),'null parent entry requires namespaced original-reference evidence')
        if lineage and lineage['operation']=='reprocessing':
            require(contexts[entry['context_id']]['value']['algorithm']['status']=='known' and contexts[entry['context_id']]['value']['settings']['status']=='known','reprocessing requires known algorithm/settings')
        payloads=entry['payloads']; require(len({p['id'] for p in payloads})==len(payloads),'payload ID unique')
        require([(ROLES.index(p['role']),p['id']) for p in payloads]==sorted((ROLES.index(p['role']),p['id']) for p in payloads),'payload order')
        data_by_id={}; by_id={p['id']:p for p in payloads}; role_payload={}
        for p in payloads:
            role=p['role']; geometry(p['geometry']); extensions(p,value['required_features'])
            if role!='module_extension':
                require(role not in role_payload and p['id']==role,'core payload identity','invalid_payload'); role_payload[role]=p
                require(any(x['role']==role and x['encoding_id']==p['encoding_id'] for x in stream['encodings']),'unoffered encoding')
            else: require('namespace' in p,'extension payload namespace')
            require(role not in ('temperature','validity','native_samples','acquisition') or p['lossiness']=='lossless','lossy evidence','invalid_payload')
            dtype=p['dtype']; size=int(p['length']); shape=p['shape']
            if dtype=='bytes': require(shape==[size] and p['layout']=='opaque' and p['geometry'] is None and p['unit'] is None and p['byte_order']=='not_applicable','opaque descriptor','invalid_payload')
            else:
                g=p['geometry']; require(g is not None and p['layout']=='row_major','numeric geometry','invalid_payload')
                require(shape[:2]==[g['height_px'],g['width_px']] and len(shape) in (2,3) and (len(shape)!=3 or 1<=shape[2]<=16),'numeric shape','invalid_payload')
                require(math.prod(shape)*WIDTH[dtype]==size,'shape byte size','invalid_payload')
                require((p['byte_order']=='not_applicable')==(WIDTH[dtype]==1),'numeric endian','invalid_payload')
            if p['kind']=='materialized':
                require(p['block_id'] in blocks,'payload block','invalid_payload')
                b=next(x for x in value['blocks'] if x['id']==p['block_id'])
                require(group(p)==group(b),'payload block group','invalid_payload')
                offset=int(p['offset']); data=blocks[b['id']][offset:offset+size]
                require(len(data)==size,'slice bounds','invalid_payload')
                slices[b['id']].append((int(entry['entry_id']),p['id'],offset,size))
                data_by_id[p['id']]=data
        for p in payloads:
            if p['kind']=='view':
                require(VIEW in value['required_features'] and p['role']=='native_samples','view feature/role','invalid_payload')
                parent=by_id.get(p['parent_payload_id'])
                require(parent is not None and parent['kind']=='materialized' and parent['role']=='acquisition','view parent','invalid_payload')
                offset=int(p['offset']); size=int(p['length'])
                require(offset%WIDTH.get(p['dtype'],1)==0 and offset+size<=int(parent['length']),'view bounds/alignment','invalid_payload')
                data_by_id[p['id']]=data_by_id[parent['id']][offset:offset+size]
            data=data_by_id[p['id']]
            require(sha(data)==p['hash'],'logical hash','committed_payload_integrity')
            logical[entry['entry_id']+':'+p['id']]=p['hash']
            if p['encoding_id']=='org.lmthermal.ht301.raw14': require(p['dtype']=='u16' and p['byte_order']=='little' and all(v[0]<0x4000 for v in struct.iter_unpack('<H',data)),'HT301 raw14 words','invalid_payload')
        measurement=entry['measurement']; available=measurement['status']=='available'
        require(('temperature' in role_payload)==available,'temperature availability','invalid_payload')
        if not available: require('validity' not in role_payload,'absent measurement mask','invalid_payload')
        for role in ROLES[:-1]:
            if role=='validity': continue
            retention=entry['retention'][role]; present=retention['status']=='present'
            actual=role in role_payload if role!='calibration_settings' else False
            require(present==actual,'retention '+role,'invalid_payload')
            require(('reason' in retention)==(not present),'retention reason')
            require(retention['status']!='omitted' or role not in stream['policy']['requested_roles'],'requested evidence silently omitted')
            require(not present or stream['capabilities'][role]!='unsupported','unsupported present')
        calibration=entry['retention']['calibration_settings']; context=contexts[entry['context_id']]['value']
        require(('reason' in calibration)==(calibration['status']!='present'),'calibration reason')
        if calibration['status']=='present': require(any(context[k]['status'] in ('known','partial') for k in ('calibration','settings')) or any(p['role']=='module_extension' for p in payloads),'missing retained calibration')
        actual_stats=None; mask=None; temps=None
        if available:
            require(stream['policy']['level']!='visual','visual policy discarded radiometry')
            p=role_payload['temperature']; g=stream['geometry']
            require(p['kind']=='materialized' and p['dtype']=='f32' and p['byte_order']=='little' and p['unit']=='Cel' and p['geometry']==g and p['shape']==[g['height_px'],g['width_px']] and p['encoding_id']=='org.lmthermal.temperature.f32le-v1','temperature descriptor','invalid_payload')
            require(measurement['temperature_payload_id']==p['id'],'measurement payload ID')
            temps=data_by_id[p['id']]; count=len(temps)//4; mask=bytes([1])*count
            if 'validity' in role_payload:
                p=role_payload['validity']; require(p['kind']=='materialized' and p['dtype']=='u8' and p['byte_order']=='not_applicable' and p['unit'] is None and p['geometry']==g and p['shape']==[g['height_px'],g['width_px']] and p['encoding_id']=='org.lmthermal.validity.u8-v1','mask descriptor','invalid_payload')
                require(measurement.get('mask_payload_id')==p['id'],'mask reference')
                mask=data_by_id[p['id']]; require(all(v in (0,1) for v in mask),'mask bit','invalid_payload')
            else: require('mask_payload_id' not in measurement,'missing mask')
            for i,(temp,) in enumerate(struct.iter_unpack('<f',temps)):
                require(math.isfinite(temp) if mask[i] else temps[4*i:4*i+4]==b'\0'*4,'finite valid/+zero invalid','invalid_payload')
            status='all_valid' if all(mask) else 'no_valid_pixels' if not any(mask) else 'partially_valid'
            require(status==measurement['validity'],'validity count','invalid_payload')
            actual_stats=statistics(temps,mask,g['width_px'])
            if 'statistics' in measurement:
                require(measurement['statistics']['temperature_payload_id']==measurement['temperature_payload_id'],'statistics payload'); check_statistics(measurement['statistics'],actual_stats)
            provenance=measurement['provenance']
            if stream['source']['module_id']=='ht301' or 'ht301' in stream['source']['module_id']:
                if provenance['kind']=='native_equivalent': require(provenance['physical_accuracy']=='not_independently_validated' and provenance.get('warning')=='Native-equivalent temperatures; absolute physical accuracy not yet independently validated.','HT accuracy warning')
            if provenance['physical_accuracy']=='independently_validated': require('validation_evidence' in provenance,'physical validation evidence')
        if 'presentation' in entry:
            presentation=entry['presentation']; measurable=available and any(mask)
            require((presentation['range_mode']=='none')==(not measurable),'presentation availability')
            if measurable: require(presentation['effective_bounds'] is not None and presentation['effective_bounds']['min']<presentation['effective_bounds']['max'],'presentation bounds')
            else: require(presentation['effective_bounds'] is None,'unavailable bounds')
        if 'analysis' in entry:
            analysis=entry['analysis']; utc(analysis['creation_time']); ids=[x['id'] for key in ('points','rectangles','annotations') for x in analysis[key]]
            require(len(set(ids))==len(ids),'analysis IDs')
            g=stream['geometry']; require(g is not None or not analysis['points'] and not analysis['rectangles'],'analysis geometry')
            for p in analysis['points']:
                require(p['x_px']<g['width_px'] and p['y_px']<g['height_px'],'point bounds')
                if 'temperature_c' in p:
                    index=p['y_px']*g['width_px']+p['x_px']; require(available and mask[index],'point invalid Celsius')
                    expected=struct.unpack_from('<f',temps,index*4)[0]; require(abs(float(p['temperature_c'])-expected)<=max(1e-4,1e-6*abs(expected)),'point cache','invalid_payload')
            for rectangle in analysis['rectangles']:
                bounds=rectangle['bounds']; x1,y1,x2,y2=[bounds[k] for k in ('x1_px','y1_px','x2_px','y2_px')]
                require(0<=x1<x2<=g['width_px'] and 0<=y1<y2<=g['height_px'],'rectangle bounds')
                if 'statistics' in rectangle:
                    require(available,'unavailable ROI Celsius'); check_statistics(rectangle['statistics'],statistics(temps,mask,g['width_px'],(x1,y1,x2,y2)))
            for a in analysis['annotations']: require(a['target_id'] is None or a['target_id'] in ids,'annotation target')
    for b in value['blocks']:
        cursor=0
        for _,_,offset,size in sorted(slices[b['id']]): require(offset==cursor,'group coverage/order','invalid_payload'); cursor+=size
        require(cursor==len(blocks[b['id']]),'unreferenced block bytes','invalid_payload')
    require(used_contexts==set(contexts) and used_segments==set(segments),'unused closure')
    return {'logical_hashes':logical,'stream_ids':sorted(stream_ids),'physical_bytes':physical}

class Timeline:
    """Constant-active-state ordered validation; context definitions are chunk-local."""
    # Retain bounded current-segment state; no duration-sized frame history is allocated.
    def __init__(self): self.next=0; self.time=0; self.active=None; self.sequences={}; self.pending_gaps={}; self.devices={}; self.definition=None; self.clocks={}
    # Track contiguous recorder-intake chronology and explicit counters without deriving sensor time.
    def accept(self, chunk):
        definitions={s['id']:s for s in chunk['segments']}
        for e in chunk['entries']:
            require(int(e['entry_id'])==self.next and int(e['timeline_ns'])>=self.time,'entry order/time')
            self.next+=1; self.time=int(e['timeline_ns']); kind=e['kind']; segment=definitions[e['segment_id']]
            if kind=='segment_start':
                require(self.active is None and int(segment['start_entry'])==self.next-1 and int(segment['start_timeline_ns'])==self.time,'segment start')
                for stream in segment['streams']:
                    for clock in stream['clocks']:
                        definition=(clock['kind'],clock['unit'],dumps(clock['tick_period_ns']))
                        require(clock['id'] not in self.clocks or self.clocks[clock['id']]==definition,'clock domain interpretation changed')
                        require(len(self.clocks)<65536 or clock['id'] in self.clocks,'clock reporting budget','local_resource_limit')
                        self.clocks[clock['id']]=definition
                self.active=e['segment_id']; self.definition=segment; self.sequences={}; self.pending_gaps={}; self.devices={}
            else:
                require(self.active==e['segment_id'] and segment==self.definition,'inactive/changed segment')
                if kind=='segment_end': self.active=None; self.definition=None
                if kind=='gap' and e['observation_first'] is not None:
                    stream=e['stream_id']; first=int(e['observation_first']); last=int(e['observation_last'])
                    if stream in self.sequences:
                        cursor=self.pending_gaps.get(stream,self.sequences[stream]+1)
                        require(first==cursor,'gap observation continuity'); self.pending_gaps[stream]=last+1
                if kind=='observation':
                    stream=e['stream_id']; sequence=int(e['observation_sequence']); prior=self.sequences.get(stream)
                    if prior is not None:
                        require(sequence>prior,'observation counter decrease')
                        require(self.pending_gaps.get(stream,prior+1)==sequence,'unexplained observation sequence skip')
                    if e['device_sequence'] is not None:
                        device=int(e['device_sequence']); previous=self.devices.get(stream)
                        require(previous is None or device>previous,'device sequence reset/decrease')
                        self.devices[stream]=device
                    self.pending_gaps.pop(stream,None); self.sequences[stream]=sequence
