"""Durable bounded team plans and single-attempt canonical agent executions."""
import hashlib
import json
from uuid import UUID,uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from salience.agents.execution import AgentExecutionContext
from salience.agents.parallel_contracts import ParallelTeamRequest
from salience.cycles.authority import current_authority
from salience.observability.tracing import TraceContext

TERMINAL={'succeeded','failed','unknown','cancelled','timed_out','held'}

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def context_payload(context):
    return {'run_id':str(context.run_id),'workspace_id':str(context.workspace_id),
            'trace':context.trace_context.to_carrier(), 'tool_scopes':sorted(context.tool_scopes),
            'memory_scopes':sorted(context.memory_scopes),'delegated_authority':sorted(context.delegated_authority)}

def parent_context(payload):
    return AgentExecutionContext(run_id=UUID(payload['run_id']),workspace_id=UUID(payload['workspace_id']),
        trace_context=TraceContext.from_carrier(payload['trace']),tool_scopes=frozenset(payload['tool_scopes']),
        memory_scopes=frozenset(payload['memory_scopes']),delegated_authority=frozenset(payload['delegated_authority']))

class ParallelTeamStore:
    def __init__(self,database_url,*,workspace_id,service):
        self.database_url=database_url.replace('postgresql+asyncpg://','postgresql://',1)
        self.workspace_id=UUID(str(workspace_id));self.service=service

    def connect(self):
        connection=psycopg.connect(self.database_url,row_factory=dict_row,connect_timeout=3)
        connection.execute("SET LOCAL statement_timeout='3s'")
        return connection

    def lock_workspace(self,c):
        c.execute('INSERT INTO parallel_agent_limits(workspace_id) VALUES(%s) ON CONFLICT DO NOTHING',(self.workspace_id,))
        c.execute('SELECT * FROM parallel_agent_limits WHERE workspace_id=%s FOR UPDATE',(self.workspace_id,)).fetchone()

    def manifest(self,agent_id):
        m=self.service.describe_agent(agent_id)
        if m.status!='enabled' or m.effect_classification not in {'read','none','pure'} or not m.supports_async:
            raise PermissionError('enabled read-only asynchronous agent required')
        if agent_id not in self.service._runtimes:raise LookupError('agent runtime unavailable')
        return m

    def _version(self,c,m):
        fields=['agent_id','version','input_schema','output_schema','tool_scopes','memory_scopes','effect_classification','supports_sync','supports_async','timeout_seconds','protocol_compatibility','trust_classification','delegated_authority_scopes']
        values=m.model_dump();args=[Jsonb(values[k]) if isinstance(values[k],(list,dict)) else values[k] for k in fields]
        c.execute('INSERT INTO agent_versions('+','.join(fields)+') VALUES('+','.join(['%s']*len(fields))+') ON CONFLICT(agent_id,version) DO NOTHING',args)
        row=c.execute('SELECT * FROM agent_versions WHERE agent_id=%s AND version=%s',(m.agent_id,m.version)).fetchone()
        if row['status']!='enabled' or any(row[k]!=values[k] for k in fields):raise PermissionError('canonical agent version differs from admitted manifest')
        return row['id']

    def _event(self,c,run_id,kind,payload):
        run=c.execute('SELECT trace_id,span_id FROM agent_runs WHERE id=%s',(run_id,)).fetchone()
        c.execute('INSERT INTO agent_events(agent_run_id,event_type,payload,trace_id,span_id) VALUES(%s,%s,%s,%s,%s)',(run_id,kind,Jsonb(payload),run['trace_id'],run['span_id']))

    def submit(self,actor_id,request,idempotency_key):
        request=ParallelTeamRequest.model_validate(request.model_dump(mode="json") if isinstance(request,ParallelTeamRequest) else request)
        actor_id=UUID(str(actor_id))
        if not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=256 or not idempotency_key.strip():raise ValueError('bounded idempotency key required')
        raw=request.model_dump(mode='json');digest=fingerprint(raw)
        with self.connect() as c:
            self.lock_workspace(c)
            authority=current_authority(c,self.workspace_id,actor_id,'agents:delegate')
            existing=c.execute('SELECT id,fingerprint FROM parallel_agent_teams WHERE workspace_id=%s AND actor_id=%s AND idempotency_key=%s',(self.workspace_id,actor_id,idempotency_key)).fetchone()
            if existing:
                if existing['fingerprint']!=digest:raise ValueError('idempotency key conflicts with original plan')
                return self._inspect(c,existing['id'])
            lead=self.manifest(request.lead_agent_id)
            manifests=[self.manifest(task.agent_id) for task in request.tasks]
            for task,m in zip(request.tasks,manifests,strict=True):self.service._validate(m.input_schema,task.input,'input')
            # Caller needs an independent current grant for every permission the lead may delegate.
            for scope in sorted(set(lead.tool_scopes+lead.memory_scopes+lead.delegated_authority_scopes)):
                current_authority(c,self.workspace_id,actor_id,scope)
            team_id=uuid4();trace=TraceContext.new_root()
            parent=self.service.create_parent(lead.agent_id,raw,run_id=team_id,trace_context=trace,workspace_id=self.workspace_id)
            version=self._version(c,lead)
            c.execute('INSERT INTO agent_runs(id,workspace_id,agent_version_id,state,input_payload,trace_id,span_id) VALUES(%s,%s,%s,\'delegating\',%s,%s,%s)',(team_id,self.workspace_id,version,Jsonb(raw),trace.trace_id,trace.span_id))
            pinned=raw|{'lead_manifest':lead.model_dump(mode='json')}
            ctx=context_payload(parent)|{'authority':authority}
            c.execute('INSERT INTO parallel_agent_teams(id,workspace_id,actor_id,idempotency_key,fingerprint,plan,parent_context,max_concurrency,deadline) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+(%s * interval \'1 second\'))',(team_id,self.workspace_id,actor_id,idempotency_key,digest,Jsonb(pinned),Jsonb(ctx),request.max_concurrency,request.timeout_seconds))
            for ordinal,(task,m) in enumerate(zip(request.tasks,manifests,strict=True)):
                child_id=uuid4();child_trace=trace.new_child();version=self._version(c,m)
                child_ctx={'trace':child_trace.to_carrier(),'tool_scopes':sorted(set(parent.tool_scopes)&set(m.tool_scopes)),
                    'memory_scopes':sorted(set(parent.memory_scopes)&set(m.memory_scopes)),
                    'delegated_authority':sorted(set(parent.delegated_authority)&set(m.delegated_authority_scopes))}
                c.execute('INSERT INTO agent_runs(id,workspace_id,agent_version_id,parent_agent_run_id,input_payload,trace_id,span_id) VALUES(%s,%s,%s,%s,%s,%s,%s)',(child_id,self.workspace_id,version,team_id,Jsonb(task.input),trace.trace_id,child_trace.span_id))
                c.execute('INSERT INTO parallel_agent_tasks(id,team_run_id,workspace_id,ordinal,task_key,agent_manifest,execution_context) VALUES(%s,%s,%s,%s,%s,%s,%s)',(child_id,team_id,self.workspace_id,ordinal,task.key,Jsonb(m.model_dump(mode='json')),Jsonb(child_ctx)))
                c.execute('INSERT INTO agent_delegations(parent_agent_run_id,child_agent_run_id,delegated_scopes,delegated_authority) VALUES(%s,%s,%s,%s)',(team_id,child_id,Jsonb(child_ctx['tool_scopes']),Jsonb(child_ctx['delegated_authority'])))
                self._event(c,child_id,'agent.queued',{'team_run_id':str(team_id),'task_key':task.key})
            self._event(c,team_id,'team.admitted',{'tasks':len(request.tasks),'max_concurrency':request.max_concurrency})
            return self._inspect(c,team_id)

    def _inspect(self,c,team_id):
        team=c.execute('SELECT * FROM parallel_agent_teams WHERE id=%s AND workspace_id=%s',(team_id,self.workspace_id)).fetchone()
        if not team:raise LookupError('team run unavailable in current workspace')
        tasks=c.execute('SELECT * FROM parallel_agent_tasks WHERE team_run_id=%s ORDER BY ordinal',(team_id,)).fetchall()
        states=[task['state'] for task in tasks]
        status='running' if 'running' in states else 'queued' if 'queued' in states else 'succeeded' if all(s=='succeeded' for s in states) else 'cancelled' if team['cancelled'] else 'partial' if 'succeeded' in states else 'failed'
        return {'run_id':str(team_id),'team_id':team['plan']['team_id'],'lead_agent_id':team['plan']['lead_agent_id'],'status':status,'cancelled':team['cancelled'],
            'max_concurrency':team['max_concurrency'],'deadline':team['deadline'].isoformat(),'max_spend_micros':0,
            'tasks':[{'key':t['task_key'],'run_id':str(t['id']),'parent_run_id':str(team_id),'agent_id':t['agent_manifest']['agent_id'],'status':t['state'],'output':t['output'],'error_type':t['error_type']} for t in tasks]}

    def inspect(self,actor_id,team_id):
        with self.connect() as c:
            self.lock_workspace(c)
            current_authority(c,self.workspace_id,actor_id,'agents:read')
            self.reconcile(c)
            return self._inspect(c,UUID(str(team_id)))

    def _terminal(self,c,task,state,error=None,output=None):
        c.execute('UPDATE parallel_agent_tasks SET state=%s,error_type=%s,output=%s,claim_id=NULL,lease_until=NULL,finished_at=clock_timestamp() WHERE id=%s',(state,error,Jsonb(output) if output is not None else None,task['id']))
        c.execute('UPDATE agent_runs SET state=%s,output_payload=%s,finished_at=clock_timestamp() WHERE id=%s',(state,Jsonb(output) if output is not None else None,task['id']))
        self._event(c,task['id'],'agent.'+state,{'error_type':error})
        self._aggregate(c,task['team_run_id'])

    def _aggregate(self,c,team_id):
        result=self._inspect(c,team_id)
        if result['status'] in {'running','queued'}:return
        changed=c.execute("UPDATE agent_runs SET state=%s,output_payload=%s,finished_at=clock_timestamp() WHERE id=%s AND state='delegating' RETURNING id",(result['status'],Jsonb(result),team_id)).fetchone()
        if changed:self._event(c,team_id,'team.finished',{'status':result['status']})

    def reconcile(self,c):
        rows=c.execute('''SELECT t.*,p.deadline,p.cancelled FROM parallel_agent_tasks t JOIN parallel_agent_teams p ON p.id=t.team_run_id
            WHERE t.workspace_id=%s AND t.state IN ('queued','running') AND (p.cancelled OR p.deadline<=clock_timestamp() OR t.lease_until<=clock_timestamp())
            ORDER BY t.team_run_id,t.ordinal LIMIT 256 FOR UPDATE OF t''',(self.workspace_id,)).fetchall()
        for task in rows:
            state='unknown' if task['state']=='running' else 'cancelled' if task['cancelled'] else 'timed_out'
            self._terminal(c,task,state,'Interrupted' if state=='unknown' else 'Cancelled' if state=='cancelled' else 'DeadlineExceeded')

    def cancel(self,actor_id,team_id):
        with self.connect() as c:
            self.lock_workspace(c);current_authority(c,self.workspace_id,actor_id,'agents:delegate')
            snapshot=self._inspect(c,team_id)
            if snapshot['status'] not in {'running','queued'}:return snapshot
            changed=c.execute('UPDATE parallel_agent_teams SET cancelled=true WHERE id=%s AND workspace_id=%s AND NOT cancelled RETURNING id',(team_id,self.workspace_id)).fetchone()
            if changed:self._event(c,team_id,'team.cancelled',{})
            self.reconcile(c)
            return self._inspect(c,team_id)

    def claim(self):
        with self.connect() as c:
            self.lock_workspace(c);self.reconcile(c)
            limit=c.execute('SELECT max_running FROM parallel_agent_limits WHERE workspace_id=%s',(self.workspace_id,)).fetchone()['max_running']
            running=c.execute("SELECT count(*) AS n FROM parallel_agent_tasks WHERE workspace_id=%s AND state='running'",(self.workspace_id,)).fetchone()['n']
            if running>=limit:return None
            candidates=c.execute('''SELECT t.*,p.actor_id,p.plan,p.parent_context,p.max_concurrency,p.deadline,a.input_payload FROM parallel_agent_tasks t
                JOIN parallel_agent_teams p ON p.id=t.team_run_id JOIN agent_runs a ON a.id=t.id
                WHERE t.workspace_id=%s AND t.state='queued' AND NOT p.cancelled AND p.deadline>clock_timestamp()
                AND (SELECT count(*) FROM parallel_agent_tasks active WHERE active.team_run_id=p.id AND active.state='running')<p.max_concurrency
                ORDER BY p.created_at,t.ordinal LIMIT 64 FOR UPDATE OF t''',(self.workspace_id,)).fetchall()
            for task in candidates:
                active=c.execute("SELECT count(*) AS n FROM parallel_agent_tasks WHERE team_run_id=%s AND state='running'",(task['team_run_id'],)).fetchone()['n']
                if active>=task['max_concurrency']:continue
                try:
                    authority=current_authority(c,self.workspace_id,task['actor_id'],'agents:delegate')
                    if authority!=task['parent_context']['authority']:raise PermissionError('admitted authority changed')
                    lead=self.manifest(task['plan']['lead_agent_id']);member=self.manifest(task['agent_manifest']['agent_id'])
                    if lead.model_dump(mode='json')!=task['plan']['lead_manifest'] or member.model_dump(mode='json')!=task['agent_manifest']:raise PermissionError('pinned agent version changed')
                    for scope in sorted(set(task['parent_context']['tool_scopes']+task['parent_context']['memory_scopes']+task['parent_context']['delegated_authority'])):current_authority(c,self.workspace_id,task['actor_id'],scope)
                    for manifest in (lead,member):
                        canonical=c.execute('SELECT status FROM agent_versions WHERE agent_id=%s AND version=%s',(manifest.agent_id,manifest.version)).fetchone()
                        if not canonical or canonical['status']!='enabled':raise PermissionError('canonical agent version disabled')
                except (PermissionError,LookupError):
                    self._terminal(c,task,'held','AuthorityChanged');continue
                token=uuid4();remaining=c.execute('SELECT EXTRACT(epoch FROM (%s-clock_timestamp())) AS seconds',(task['deadline'],)).fetchone()['seconds']
                timeout=min(float(remaining),member.timeout_seconds)
                try:
                    # Eligibility can expire before this statement. Preserve the outer
                    # transaction so normal deadline/cancellation reconciliation can run.
                    with c.transaction():
                        c.execute("UPDATE parallel_agent_tasks SET state='running',claim_id=%s,lease_until=LEAST(%s,clock_timestamp()+(%s * interval '1 second')),started_at=clock_timestamp() WHERE id=%s",(token,task['deadline'],timeout,task['id']))
                except psycopg.errors.RaiseException as error:
                    if error.diag.message_primary!='parallel concurrency or deadline bound':raise
                    self.reconcile(c)
                    return None
                c.execute("UPDATE agent_runs SET state='running',started_at=clock_timestamp() WHERE id=%s",(task['id'],))
                self._event(c,task['id'],'agent.started',{'claim_id':str(token)})
                return task|{'claim_id':token,'timeout_seconds':timeout}
            return None

    def finish(self,task,*,state,output=None,error=None):
        if state not in {'succeeded','failed','unknown','cancelled'}:raise ValueError('invalid terminal outcome')
        if output is not None:
            try:
                if len(json.dumps(output,allow_nan=False).encode())>65536:raise ValueError()
            except (TypeError,ValueError):state='failed';output=None;error='InvalidOrOversizedOutput'
        with self.connect() as c:
            self.lock_workspace(c);self.reconcile(c)
            current=c.execute('SELECT * FROM parallel_agent_tasks WHERE id=%s AND workspace_id=%s FOR UPDATE',(task['id'],self.workspace_id)).fetchone()
            if not current or current['state']!='running' or current['claim_id']!=task['claim_id']:return False
            self._terminal(c,current,state,error,output)
            return True

    def active(self,task):
        with self.connect() as c:
            row=c.execute("SELECT state,claim_id FROM parallel_agent_tasks WHERE id=%s AND workspace_id=%s",(task['id'],self.workspace_id)).fetchone()
            if not row or row['state']!='running' or row['claim_id']!=task['claim_id']:return False
            try:
                authority=current_authority(c,self.workspace_id,task['actor_id'],'agents:delegate')
                if authority!=task['parent_context']['authority']:return False
                for scope in sorted(set(task['parent_context']['tool_scopes']+task['parent_context']['memory_scopes']+task['parent_context']['delegated_authority'])):current_authority(c,self.workspace_id,task['actor_id'],scope)
                for pinned in (task['plan']['lead_manifest'],task['agent_manifest']):
                    if self.manifest(pinned['agent_id']).model_dump(mode='json')!=pinned:return False
                    row=c.execute('SELECT status FROM agent_versions WHERE agent_id=%s AND version=%s',(pinned['agent_id'],pinned['version'])).fetchone()
                    if not row or row['status']!='enabled':return False
                return True
            except (PermissionError,LookupError):return False
